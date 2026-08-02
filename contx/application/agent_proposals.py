"""Validated agent proposal inbox with no direct final-memory write path."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import Engine

from contx.db import session_scope
from contx.db.repositories import (
    AgentProposalAdoptionRepository,
    AgentProposalRepository,
    PatternRepository,
    PipelineRepository,
)
from contx.errors import DatabaseError, PipelineError
from contx.memory_store import AgentProposalEvaluator, MemoryStore
from contx.models import (
    AgentProposal,
    AgentProposalAdoptionBuild,
    AgentProposalDecision,
    AgentProposalReasonCode,
    AgentProposalStatus,
    AgentProposalType,
    AgentRole,
    CandidateStatus,
    Clock,
    IdentifierSource,
    MemoryCandidate,
    MemoryLink,
    MemoryLinkStatus,
    MemoryProvenance,
    PatternStatus,
    ProposalReferenceType,
    Sensitivity,
)
from contx.models.common import build_idempotency_key

OPTMEM_PROPOSAL_BYTES = 280
AGENT_PROPOSAL_ADOPTION_VERSION = "agent-proposal-adoption-v1"
DEFAULT_MINIMUM_ADOPTION_CONFIDENCE = 0.75
DEFAULT_ACTIVE_MEMORY_COUNT = 32
DEFAULT_ACTIVE_MEMORY_BYTES = 8 * 1024


@dataclass(frozen=True, slots=True)
class AgentProposalAdoptionResult:
    proposal: AgentProposal
    build: AgentProposalAdoptionBuild
    candidate: MemoryCandidate | None
    memory_link: MemoryLink | None
    replayed: bool
    maintenance_required: bool


@dataclass(frozen=True, slots=True)
class _ReferenceEvidence:
    serialized: str
    pattern_ids: tuple[UUID, ...]
    event_ids: tuple[UUID, ...]
    observation_ids: tuple[UUID, ...]
    sensitivity: Sensitivity
    confidence: float


class AgentProposalService:
    """Validate and retain proposals without allowing direct memory appends."""

    def __init__(
        self,
        *,
        engine: Engine,
        clock: Clock,
        identifiers: IdentifierSource,
    ) -> None:
        self._engine = engine
        self._clock = clock
        self._identifiers = identifiers

    def submit(
        self,
        *,
        agent_id: str,
        agent_role: AgentRole,
        text: str,
        reference_type: ProposalReferenceType | None = None,
        reference_id: UUID | None = None,
    ) -> AgentProposal:
        normalized_agent = agent_id.strip()
        normalized_text = text.strip()
        if (
            not normalized_agent
            or len(normalized_agent) > 120
            or any(character in normalized_agent for character in "\r\n")
        ):
            raise PipelineError("Agent proposal identity is invalid")
        if (
            not normalized_text
            or len(normalized_text) > 4000
            or any(character in normalized_text for character in "\r\n")
        ):
            raise PipelineError("Agent proposal text must be one bounded line")
        if (reference_type is None) != (reference_id is None):
            raise PipelineError(
                "Agent proposal reference type and identifier must be supplied together"
            )

        status, reason = self._classify(
            agent_role=agent_role,
            text=normalized_text,
            reference_type=reference_type,
            reference_id=reference_id,
        )
        now = self._clock.now()
        proposal = AgentProposal(
            id=self._identifiers.new(),
            idempotency_key=build_idempotency_key(
                "agent-proposal-v1",
                normalized_agent,
                agent_role,
                normalized_text,
                reference_type,
                reference_id,
            ),
            agent_id=normalized_agent,
            agent_role=agent_role,
            text=normalized_text,
            proposal_type=AgentProposalType.MEMORY,
            reference_type=reference_type,
            reference_id=reference_id,
            status=status,
            reason=reason,
            created_at=now,
            processed_at=(None if status is AgentProposalStatus.PENDING else now),
        )
        with session_scope(self._engine) as database_session:
            return AgentProposalRepository(database_session).save(proposal)

    def _classify(
        self,
        *,
        agent_role: AgentRole,
        text: str,
        reference_type: ProposalReferenceType | None,
        reference_id: UUID | None,
    ) -> tuple[AgentProposalStatus, str | None]:
        if agent_role is AgentRole.SUBAGENT:
            return (
                AgentProposalStatus.REJECTED,
                "subagent_proposals_require_primary_adoption",
            )
        if reference_type is None or reference_id is None:
            return AgentProposalStatus.DEFERRED, "provenance_reference_required"
        if len(text.encode("utf-8")) > OPTMEM_PROPOSAL_BYTES:
            return (
                AgentProposalStatus.DEFERRED,
                "proposal_exceeds_memory_backend_limit",
            )

        with session_scope(self._engine) as database_session:
            pipeline = PipelineRepository(database_session)
            if reference_type is ProposalReferenceType.EVENT:
                event = pipeline.event_by_id(reference_id)
                if event is None:
                    return AgentProposalStatus.REJECTED, "reference_not_found"
                eligible = event.sensitivity.permits_durable_memory
            elif reference_type is ProposalReferenceType.PATTERN:
                pattern = PatternRepository(database_session).by_id(reference_id)
                if pattern is None:
                    return AgentProposalStatus.REJECTED, "reference_not_found"
                if pattern.status_at(self._clock.now()) is not PatternStatus.ACTIVE:
                    return AgentProposalStatus.DEFERRED, "reference_pattern_expired"
                eligible = pattern.sensitivity.permits_durable_memory
            else:
                link = pipeline.memory_link_by_id(reference_id)
                if link is None:
                    return AgentProposalStatus.REJECTED, "reference_not_found"
                if link.status is not MemoryLinkStatus.ACTIVE:
                    return AgentProposalStatus.REJECTED, "reference_memory_not_active"
                candidate = pipeline.candidate_by_id(link.candidate_id)
                if candidate is None:
                    return AgentProposalStatus.REJECTED, "reference_not_found"
                eligible = candidate.sensitivity.permits_durable_memory
        if not eligible:
            return AgentProposalStatus.REJECTED, "reference_sensitivity_not_eligible"
        return AgentProposalStatus.PENDING, None


class AgentProposalAdoptionService:
    """Validate, stage, append, and finalize one user-selected proposal."""

    def __init__(
        self,
        *,
        engine: Engine,
        memory_store: MemoryStore,
        evaluator: AgentProposalEvaluator,
        clock: Clock,
        minimum_confidence: float = DEFAULT_MINIMUM_ADOPTION_CONFIDENCE,
        max_memory_bytes: int = OPTMEM_PROPOSAL_BYTES,
        max_active_memories: int = DEFAULT_ACTIVE_MEMORY_COUNT,
        max_active_memory_bytes: int = DEFAULT_ACTIVE_MEMORY_BYTES,
    ) -> None:
        if not 0.5 <= minimum_confidence <= 1.0:
            raise ValueError("proposal adoption confidence threshold is invalid")
        if not 64 <= max_memory_bytes <= 4096:
            raise ValueError("proposal adoption memory byte limit is invalid")
        if not 1 <= max_active_memories <= 128:
            raise ValueError("proposal active-memory count is invalid")
        if not 1024 <= max_active_memory_bytes <= 32 * 1024:
            raise ValueError("proposal active-memory byte limit is invalid")
        self._engine = engine
        self._memory_store = memory_store
        self._evaluator = evaluator
        self._clock = clock
        self._minimum_confidence = minimum_confidence
        self._max_memory_bytes = max_memory_bytes
        self._max_active_memories = max_active_memories
        self._max_active_memory_bytes = max_active_memory_bytes

    def adopt(self, *, proposal_id: UUID) -> AgentProposalAdoptionResult:
        candidate: MemoryCandidate | None = None
        pending_link: MemoryLink | None = None
        with session_scope(self._engine) as database_session:
            proposal_repository = AgentProposalRepository(database_session)
            adoption_repository = AgentProposalAdoptionRepository(database_session)
            pipeline = PipelineRepository(database_session)
            proposal = proposal_repository.by_id(proposal_id)
            if proposal is None:
                raise PipelineError("Agent proposal was not found")
            build = adoption_repository.build_by_proposal(proposal_id)
            if build is not None and build.candidate_id is not None:
                candidate = pipeline.candidate_by_id(build.candidate_id)
                pending_link = pipeline.memory_link_by_candidate_id(build.candidate_id)
                if candidate is None or pending_link is None:
                    raise DatabaseError("Agent proposal adoption staging is incomplete")
                if proposal.status is AgentProposalStatus.ADOPTED:
                    if pending_link.status is not MemoryLinkStatus.ACTIVE:
                        raise DatabaseError(
                            "Finalized agent proposal adoption is inconsistent"
                        )
                    return AgentProposalAdoptionResult(
                        proposal=proposal,
                        build=build,
                        candidate=candidate,
                        memory_link=pending_link,
                        replayed=True,
                        maintenance_required=False,
                    )
                if (
                    proposal.status is not AgentProposalStatus.PENDING
                    or pending_link.status is not MemoryLinkStatus.PENDING
                ):
                    raise DatabaseError("Agent proposal adoption replay is invalid")
            elif build is not None:
                if proposal.status.value != build.decision.value:
                    raise DatabaseError("Agent proposal decision replay is invalid")
                return AgentProposalAdoptionResult(
                    proposal=proposal,
                    build=build,
                    candidate=None,
                    memory_link=None,
                    replayed=True,
                    maintenance_required=False,
                )
            elif proposal.status is not AgentProposalStatus.PENDING:
                raise PipelineError("Only pending agent proposals can be adopted")

        if build is None:
            reference = self._load_reference(proposal)
            active_memories = self._load_active_memories(proposal.text)
            started_at = self._clock.now()
            evaluation = self._evaluator.evaluate(
                proposal=proposal.text,
                reference=reference.serialized,
                active_memories=active_memories,
                minimum_confidence=self._minimum_confidence,
            )
            ended_at = self._clock.now()
            if self._load_reference(proposal) != reference:
                raise PipelineError(
                    "Agent proposal reference changed during local validation"
                )
            if self._load_active_memories(proposal.text) != active_memories:
                raise PipelineError(
                    "Active memory changed during local proposal validation"
                )
            model_digest = self._evaluator.model_digest
            if model_digest is None:
                raise PipelineError("Local proposal model identity is unavailable")
            decision, reason_code = _validate_evaluation(
                evaluation.decision,
                evaluation.reason_code,
                evaluation.confidence,
                minimum_confidence=self._minimum_confidence,
            )
            candidate_id = (
                uuid5(
                    NAMESPACE_URL,
                    f"contx:agent-proposal-candidate:{proposal.id}",
                )
                if decision is AgentProposalDecision.ACCEPTED
                else None
            )
            build = AgentProposalAdoptionBuild(
                proposal_id=proposal.id,
                candidate_id=candidate_id,
                provider=self._evaluator.provider,
                endpoint=self._evaluator.endpoint,
                model=self._evaluator.model,
                model_digest=model_digest,
                prompt_version=self._evaluator.prompt_version,
                output_schema_version=self._evaluator.output_schema_version,
                decision=decision,
                reason_code=reason_code,
                confidence=evaluation.confidence,
                reference_sha256=_sha256(reference.serialized),
                active_memory_sha256=_sha256(
                    json.dumps(
                        active_memories,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                ),
                active_memory_count=len(active_memories),
                started_at=started_at,
                ended_at=ended_at,
                wall_duration_ms=max(
                    0,
                    int((ended_at - started_at).total_seconds() * 1000),
                ),
            )
            if decision is not AgentProposalDecision.ACCEPTED:
                with session_scope(self._engine) as database_session:
                    decided = AgentProposalAdoptionRepository(
                        database_session
                    ).record_decision(build=build, processed_at=ended_at)
                return AgentProposalAdoptionResult(
                    proposal=decided,
                    build=build,
                    candidate=None,
                    memory_link=None,
                    replayed=False,
                    maintenance_required=False,
                )
            if candidate_id is None:
                raise DatabaseError("Accepted proposal candidate identity is missing")
            _validate_memory_text(
                proposal.text,
                max_bytes=self._max_memory_bytes,
            )
            confidence = min(reference.confidence, evaluation.confidence)
            candidate_key = build_idempotency_key(
                AGENT_PROPOSAL_ADOPTION_VERSION,
                proposal.id,
            )
            candidate = MemoryCandidate(
                id=candidate_id,
                idempotency_key=candidate_key,
                text=proposal.text,
                source_type="agent_proposal",
                source_ids=(proposal.id,),
                utility=1.0,
                importance=1.0,
                durability=1.0,
                novelty=1.0,
                recurrence=0.0,
                confidence=confidence,
                ambiguity=0.0,
                redundancy=0.0,
                sensitivity=reference.sensitivity,
                score=confidence,
                scoring_version=AGENT_PROPOSAL_ADOPTION_VERSION,
                status=CandidateStatus.ACCEPTED,
                created_at=ended_at,
                processed_at=ended_at,
            )
            memory_link_id = uuid5(
                NAMESPACE_URL,
                f"contx:agent-proposal-memory:{proposal.id}",
            )
            pending_link = MemoryLink(
                id=memory_link_id,
                memory_backend_id=f"pending:{memory_link_id}",
                candidate_id=candidate.id,
                provenance=MemoryProvenance(
                    candidate_id=candidate.id,
                    pattern_ids=reference.pattern_ids,
                    event_ids=reference.event_ids,
                    observation_ids=reference.observation_ids,
                ),
                confidence=confidence,
                status=MemoryLinkStatus.PENDING,
                created_at=ended_at,
            )
            with session_scope(self._engine) as database_session:
                candidate, pending_link, build = AgentProposalAdoptionRepository(
                    database_session
                ).stage_acceptance(
                    candidate=candidate,
                    link=pending_link,
                    build=build,
                )

        if candidate is None or pending_link is None:
            raise DatabaseError("Agent proposal adoption staging is incomplete")
        self._memory_store.initialize()
        appended = self._memory_store.append(
            candidate.text,
            idempotency_key=candidate.idempotency_key,
        )
        with session_scope(self._engine) as database_session:
            adopted, stored_candidate, active_link = AgentProposalAdoptionRepository(
                database_session
            ).finalize(
                proposal_id=proposal.id,
                candidate_id=candidate.id,
                memory_link_id=pending_link.id,
                backend_id=appended.backend_id,
                processed_at=self._clock.now(),
            )
        return AgentProposalAdoptionResult(
            proposal=adopted,
            build=build,
            candidate=stored_candidate,
            memory_link=active_link,
            replayed=False,
            maintenance_required=appended.maintenance_required,
        )

    def reject(
        self,
        *,
        proposal_id: UUID,
        reason: str = "user_rejected",
    ) -> AgentProposal:
        with session_scope(self._engine) as database_session:
            return AgentProposalAdoptionRepository(database_session).reject(
                proposal_id=proposal_id,
                processed_at=self._clock.now(),
                reason=reason,
            )

    def _load_reference(self, proposal: AgentProposal) -> _ReferenceEvidence:
        if proposal.reference_type is None or proposal.reference_id is None:
            raise PipelineError("Agent proposal provenance reference is missing")
        with session_scope(self._engine) as database_session:
            pipeline = PipelineRepository(database_session)
            if proposal.reference_type is ProposalReferenceType.EVENT:
                event = pipeline.event_by_id(proposal.reference_id)
                if event is None:
                    raise PipelineError("Agent proposal reference was not found")
                reference = _ReferenceEvidence(
                    serialized=_serialize_reference(
                        {
                            "type": "event",
                            "event_type": event.type.value,
                            "summary": event.summary,
                            "facts": event.facts,
                            "projects": event.projects,
                            "entities": event.entities,
                            "epistemic_status": event.epistemic_status.value,
                            "confidence": event.confidence,
                        }
                    ),
                    pattern_ids=(),
                    event_ids=(event.id,),
                    observation_ids=event.source_observation_ids,
                    sensitivity=event.sensitivity,
                    confidence=event.confidence,
                )
            elif proposal.reference_type is ProposalReferenceType.PATTERN:
                pattern = PatternRepository(database_session).by_id(
                    proposal.reference_id
                )
                if pattern is None:
                    raise PipelineError("Agent proposal reference was not found")
                if pattern.status_at(self._clock.now()) is not PatternStatus.ACTIVE:
                    raise PipelineError(
                        "Agent proposal pattern reference is not active"
                    )
                observation_ids: list[UUID] = []
                for event_id in pattern.source_event_ids:
                    event = pipeline.event_by_id(event_id)
                    if event is None:
                        raise DatabaseError(
                            "Agent proposal pattern evidence is missing"
                        )
                    observation_ids.extend(event.source_observation_ids)
                reference = _ReferenceEvidence(
                    serialized=_serialize_reference(
                        {
                            "type": "pattern",
                            "pattern_type": pattern.type.value,
                            "summary": pattern.summary,
                            "metrics": pattern.metrics,
                            "projects": pattern.projects,
                            "entities": pattern.entities,
                            "epistemic_status": pattern.epistemic_status.value,
                            "confidence": pattern.confidence,
                            "evidence_count": pattern.evidence_count,
                        }
                    ),
                    pattern_ids=(pattern.id,),
                    event_ids=pattern.source_event_ids,
                    observation_ids=tuple(dict.fromkeys(observation_ids)),
                    sensitivity=pattern.sensitivity,
                    confidence=pattern.confidence,
                )
            else:
                link = pipeline.memory_link_by_id(proposal.reference_id)
                if link is None:
                    raise PipelineError("Agent proposal reference was not found")
                if link.status is not MemoryLinkStatus.ACTIVE:
                    raise PipelineError("Agent proposal memory reference is not active")
                candidate = pipeline.candidate_by_id(link.candidate_id)
                if candidate is None:
                    raise DatabaseError("Agent proposal memory evidence is missing")
                reference = _ReferenceEvidence(
                    serialized=_serialize_reference(
                        {
                            "type": "memory",
                            "memory": candidate.text,
                            "confidence": link.confidence,
                        }
                    ),
                    pattern_ids=link.provenance.pattern_ids,
                    event_ids=link.provenance.event_ids,
                    observation_ids=link.provenance.observation_ids,
                    sensitivity=candidate.sensitivity,
                    confidence=link.confidence,
                )
        if not reference.sensitivity.permits_durable_memory:
            raise PipelineError("Agent proposal reference is not memory-eligible")
        return reference

    def _load_active_memories(self, proposal_text: str) -> tuple[str, ...]:
        with session_scope(self._engine) as database_session:
            active_candidates = PipelineRepository(
                database_session
            ).active_memory_candidates()
        return _select_active_memories(
            proposal_text,
            tuple(candidate.text for candidate in active_candidates),
            max_count=self._max_active_memories,
            max_bytes=self._max_active_memory_bytes,
        )


def _serialize_reference(value: dict[str, object]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _select_active_memories(
    proposal: str,
    memories: tuple[str, ...],
    *,
    max_count: int,
    max_bytes: int,
) -> tuple[str, ...]:
    proposal_words = set(re.findall(r"\w+", proposal.casefold()))
    normalized_proposal = proposal.casefold().strip()
    ranked = sorted(
        enumerate(memories),
        key=lambda item: (
            item[1].casefold().strip() == normalized_proposal,
            len(proposal_words & set(re.findall(r"\w+", item[1].casefold()))),
            -item[0],
        ),
        reverse=True,
    )
    selected: list[str] = []
    used_bytes = 2
    for _, memory in ranked:
        encoded_bytes = len(memory.encode("utf-8")) + 3
        if len(selected) >= max_count:
            break
        if used_bytes + encoded_bytes > max_bytes:
            continue
        selected.append(memory)
        used_bytes += encoded_bytes
    return tuple(selected)


def _validate_memory_text(value: str, *, max_bytes: int) -> str:
    normalized = value.strip()
    if (
        not normalized
        or "\n" in normalized
        or "\r" in normalized
        or len(normalized.encode("utf-8")) > max_bytes
    ):
        raise PipelineError("Agent proposal cannot fit one final-memory line")
    return normalized


def _validate_evaluation(
    decision_value: str,
    reason_value: str,
    confidence: float,
    *,
    minimum_confidence: float,
) -> tuple[AgentProposalDecision, AgentProposalReasonCode]:
    try:
        decision = AgentProposalDecision(decision_value)
        reason = AgentProposalReasonCode(reason_value)
    except ValueError:
        raise PipelineError("Local proposal evaluation was invalid") from None
    expected = {
        AgentProposalDecision.ACCEPTED: {
            AgentProposalReasonCode.SUPPORTED_NOVEL,
        },
        AgentProposalDecision.REJECTED: {
            AgentProposalReasonCode.UNSUPPORTED_BY_REFERENCE,
            AgentProposalReasonCode.DUPLICATE_ACTIVE_MEMORY,
            AgentProposalReasonCode.CONFLICTS_WITH_ACTIVE_MEMORY,
        },
        AgentProposalDecision.DEFERRED: {
            AgentProposalReasonCode.AMBIGUOUS_REFERENCE,
        },
    }
    if (
        not 0.0 <= confidence <= 1.0
        or reason not in expected[decision]
        or (
            decision is AgentProposalDecision.ACCEPTED
            and confidence < minimum_confidence
        )
    ):
        raise PipelineError("Local proposal evaluation was invalid")
    return decision, reason
