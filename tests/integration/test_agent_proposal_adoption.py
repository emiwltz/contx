"""Explicit, local-model-validated agent proposal adoption tests."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal
from uuid import UUID

import pytest
from sqlalchemy import Engine

from contx.application import AgentProposalAdoptionService, AgentProposalService
from contx.db import create_database_engine, session_scope, upgrade_database
from contx.db.models import EventModel, MemoryLinkModel
from contx.db.repositories import (
    AgentProposalAdoptionRepository,
    AgentProposalRepository,
    PatternRepository,
    PipelineRepository,
)
from contx.errors import MemoryStoreError, PipelineError
from contx.memory_store import (
    AgentProposalEvaluation,
    MemoryAppendResult,
    RecordingMemoryStore,
)
from contx.models import (
    AgentProposal,
    AgentProposalDecision,
    AgentProposalStatus,
    AgentRole,
    CandidateStatus,
    EpistemicStatus,
    Event,
    EventType,
    MemoryCandidate,
    MemoryLink,
    MemoryLinkStatus,
    MemoryProvenance,
    Observation,
    Pattern,
    PatternType,
    ProcessingRun,
    ProcessingRunStatus,
    ProposalReferenceType,
    Sensitivity,
    SourceType,
)
from tests.helpers import FixedClock, SequenceIdentifiers

NOW = datetime(2026, 8, 2, 23, 0, tzinfo=UTC)
OBSERVATION_ID = UUID("d0000000-0000-0000-0000-000000000001")
EVENT_ID = UUID("d0000000-0000-0000-0000-000000000002")
EXISTING_CANDIDATE_ID = UUID("d0000000-0000-0000-0000-000000000003")
EXISTING_MEMORY_ID = UUID("d0000000-0000-0000-0000-000000000004")
PROPOSAL_ID = UUID("d0000000-0000-0000-0000-000000000005")
SECOND_OBSERVATION_ID = UUID("d0000000-0000-0000-0000-000000000006")
SECOND_EVENT_ID = UUID("d0000000-0000-0000-0000-000000000007")
PATTERN_RUN_ID = UUID("d0000000-0000-0000-0000-000000000008")
PATTERN_ID = UUID("d0000000-0000-0000-0000-000000000009")
PROPOSAL_TEXT = "Atlas deploys locally from the verified workspace."


def test_explicit_adoption_appends_exact_proposal_with_transitive_provenance(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path)
    memory = RecordingMemoryStore()
    evaluator = RecordingEvaluator()
    try:
        _seed_event(engine)
        _seed_existing_memory(engine, memory)
        proposal = _submit(engine)
        service = _service(engine, memory, evaluator)

        adopted = service.adopt(proposal_id=proposal.id)
        replay = service.adopt(proposal_id=proposal.id)

        assert adopted.proposal.status is AgentProposalStatus.ADOPTED
        assert adopted.build.decision is AgentProposalDecision.ACCEPTED
        assert adopted.build.model_digest == "synthetic-proposal-digest"
        assert adopted.build.reference_sha256 != ""
        assert adopted.build.active_memory_count == 1
        assert adopted.candidate is not None
        assert adopted.candidate.text == PROPOSAL_TEXT
        assert adopted.candidate.status is CandidateStatus.STORED
        assert adopted.candidate.source_type == "agent_proposal"
        assert adopted.candidate.source_ids == (proposal.id,)
        assert adopted.memory_link is not None
        assert adopted.memory_link.status is MemoryLinkStatus.ACTIVE
        assert adopted.memory_link.provenance.pattern_ids == ()
        assert adopted.memory_link.provenance.event_ids == (EVENT_ID,)
        assert adopted.memory_link.provenance.observation_ids == (OBSERVATION_ID,)
        assert replay.memory_link == adopted.memory_link
        assert replay.replayed
        assert len(evaluator.calls) == 1
        assert PROPOSAL_TEXT in evaluator.calls[0][1]
        assert evaluator.calls[0][2] == ("Atlas uses Python.",)
        assert memory.entries == ("Atlas uses Python.", PROPOSAL_TEXT)

        with session_scope(engine) as database_session:
            build = AgentProposalAdoptionRepository(database_session).build_by_proposal(
                proposal.id
            )
            assert build == adopted.build
            assert (
                AgentProposalRepository(database_session).by_id(proposal.id)
                == adopted.proposal
            )
    finally:
        engine.dispose()


def test_interruption_after_append_resumes_without_model_or_memory_duplicate(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path)
    memory = FailOnceAfterAppendMemory()
    evaluator = RecordingEvaluator()
    try:
        _seed_event(engine)
        proposal = _submit(engine)
        memory.arm_failure()
        service = _service(engine, memory, evaluator)

        with pytest.raises(MemoryStoreError, match="injected boundary failure"):
            service.adopt(proposal_id=proposal.id)

        with session_scope(engine) as database_session:
            persisted = AgentProposalRepository(database_session).by_id(proposal.id)
            build = AgentProposalAdoptionRepository(database_session).build_by_proposal(
                proposal.id
            )
            assert persisted is not None
            assert persisted.status is AgentProposalStatus.PENDING
            assert build is not None
            assert build.candidate_id is not None
            pending = PipelineRepository(database_session).memory_link_by_candidate_id(
                build.candidate_id
            )
            assert pending is not None
            assert pending.status is MemoryLinkStatus.PENDING

        recovered = service.adopt(proposal_id=proposal.id)

        assert recovered.proposal.status is AgentProposalStatus.ADOPTED
        assert recovered.memory_link is not None
        assert recovered.memory_link.status is MemoryLinkStatus.ACTIVE
        assert len(evaluator.calls) == 1
        assert evaluator.calls[0][0] == PROPOSAL_TEXT
        assert evaluator.calls[0][2:] == ((), 0.75)
        assert memory.entries == (PROPOSAL_TEXT,)
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("decision", "reason_code", "expected_status"),
    [
        ("rejected", "unsupported_by_reference", AgentProposalStatus.REJECTED),
        ("rejected", "duplicate_active_memory", AgentProposalStatus.REJECTED),
        ("deferred", "ambiguous_reference", AgentProposalStatus.DEFERRED),
    ],
)
def test_local_decisions_are_audited_without_final_memory_write(
    tmp_path: Path,
    decision: Literal["accepted", "rejected", "deferred"],
    reason_code: Literal[
        "supported_novel",
        "unsupported_by_reference",
        "duplicate_active_memory",
        "conflicts_with_active_memory",
        "ambiguous_reference",
    ],
    expected_status: AgentProposalStatus,
) -> None:
    engine = _engine(tmp_path)
    memory = RecordingMemoryStore()
    evaluator = RecordingEvaluator(decision=decision, reason_code=reason_code)
    try:
        _seed_event(engine)
        proposal = _submit(engine)
        service = _service(engine, memory, evaluator)

        decided = service.adopt(proposal_id=proposal.id)
        replay = service.adopt(proposal_id=proposal.id)

        assert decided.proposal.status is expected_status
        assert decided.proposal.reason == reason_code
        assert decided.candidate is None
        assert decided.memory_link is None
        assert replay.proposal == decided.proposal
        assert replay.replayed
        assert len(evaluator.calls) == 1
        assert memory.entries == ()
    finally:
        engine.dispose()


def test_user_rejection_is_idempotent_and_never_invokes_local_model(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path)
    memory = RecordingMemoryStore()
    evaluator = RecordingEvaluator()
    try:
        _seed_event(engine)
        proposal = _submit(engine)
        service = _service(engine, memory, evaluator)

        rejected = service.reject(proposal_id=proposal.id)
        replay = service.reject(proposal_id=proposal.id)

        assert rejected.status is AgentProposalStatus.REJECTED
        assert rejected.reason == "user_rejected"
        assert replay == rejected
        assert evaluator.calls == []
        assert memory.entries == ()
        with pytest.raises(PipelineError, match="Only pending"):
            service.adopt(proposal_id=proposal.id)
    finally:
        engine.dispose()


def test_reference_sensitivity_is_revalidated_at_adoption_time(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path)
    memory = RecordingMemoryStore()
    evaluator = RecordingEvaluator()
    try:
        _seed_event(engine)
        proposal = _submit(engine)
        with session_scope(engine) as database_session:
            event = database_session.get(EventModel, str(EVENT_ID))
            assert event is not None
            event.sensitivity = Sensitivity.SENSITIVE.value

        with pytest.raises(PipelineError, match="not memory-eligible"):
            _service(engine, memory, evaluator).adopt(proposal_id=proposal.id)

        assert evaluator.calls == []
        assert memory.entries == ()
    finally:
        engine.dispose()


def test_reference_change_during_model_call_aborts_before_staging(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path)
    memory = RecordingMemoryStore()
    evaluator = SensitivityMutatingEvaluator(engine)
    try:
        _seed_event(engine)
        proposal = _submit(engine)

        with pytest.raises(PipelineError, match="not memory-eligible"):
            _service(engine, memory, evaluator).adopt(proposal_id=proposal.id)

        assert len(evaluator.calls) == 1
        assert memory.entries == ()
        with session_scope(engine) as database_session:
            persisted = AgentProposalRepository(database_session).by_id(proposal.id)
            build = AgentProposalAdoptionRepository(database_session).build_by_proposal(
                proposal.id
            )
            assert persisted is not None
            assert persisted.status is AgentProposalStatus.PENDING
            assert build is None
    finally:
        engine.dispose()


def test_memory_reference_adoption_inherits_existing_transitive_provenance(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path)
    memory = RecordingMemoryStore()
    evaluator = RecordingEvaluator()
    try:
        _seed_event(engine)
        existing = _seed_existing_memory(engine, memory)
        proposal = _submit(
            engine,
            reference_type=ProposalReferenceType.MEMORY,
            reference_id=existing.id,
        )

        adopted = _service(engine, memory, evaluator).adopt(proposal_id=proposal.id)

        assert adopted.memory_link is not None
        assert adopted.memory_link.provenance.pattern_ids == ()
        assert adopted.memory_link.provenance.event_ids == (EVENT_ID,)
        assert adopted.memory_link.provenance.observation_ids == (OBSERVATION_ID,)
        assert '"type":"memory"' in evaluator.calls[0][1]
    finally:
        engine.dispose()


def test_memory_reference_is_revalidated_as_active_before_model_use(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path)
    memory = RecordingMemoryStore()
    evaluator = RecordingEvaluator()
    try:
        _seed_event(engine)
        existing = _seed_existing_memory(engine, memory)
        proposal = _submit(
            engine,
            reference_type=ProposalReferenceType.MEMORY,
            reference_id=existing.id,
        )
        with session_scope(engine) as database_session:
            link = database_session.get(MemoryLinkModel, str(existing.id))
            assert link is not None
            link.status = MemoryLinkStatus.SUPERSEDED.value

        with pytest.raises(PipelineError, match="not active"):
            _service(engine, memory, evaluator).adopt(proposal_id=proposal.id)

        assert evaluator.calls == []
        assert memory.entries == ("Atlas uses Python.",)
    finally:
        engine.dispose()


def test_pattern_reference_adoption_preserves_all_evidence_edges(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path)
    memory = RecordingMemoryStore()
    evaluator = RecordingEvaluator()
    try:
        _seed_event(engine)
        _seed_pattern(engine)
        proposal = _submit(
            engine,
            reference_type=ProposalReferenceType.PATTERN,
            reference_id=PATTERN_ID,
        )

        adopted = _service(engine, memory, evaluator).adopt(proposal_id=proposal.id)

        assert adopted.memory_link is not None
        assert adopted.memory_link.provenance.pattern_ids == (PATTERN_ID,)
        assert adopted.memory_link.provenance.event_ids == (
            EVENT_ID,
            SECOND_EVENT_ID,
        )
        assert adopted.memory_link.provenance.observation_ids == (
            OBSERVATION_ID,
            SECOND_OBSERVATION_ID,
        )
        assert '"type":"pattern"' in evaluator.calls[0][1]
    finally:
        engine.dispose()


class RecordingEvaluator:
    provider: Literal["ollama"] = "ollama"
    endpoint = "http://127.0.0.1:11434"
    model = "synthetic-proposal-model"
    model_digest = "synthetic-proposal-digest"
    prompt_version = "agent-proposal-adoption-v2"
    output_schema_version = "agent-proposal-adoption-output-v2"

    def __init__(
        self,
        *,
        decision: Literal["accepted", "rejected", "deferred"] = "accepted",
        reason_code: Literal[
            "supported_novel",
            "unsupported_by_reference",
            "duplicate_active_memory",
            "conflicts_with_active_memory",
            "ambiguous_reference",
        ] = "supported_novel",
        confidence: float = 0.9,
    ) -> None:
        self._result = AgentProposalEvaluation(
            decision=decision,
            reason_code=reason_code,
            confidence=confidence,
        )
        self.calls: list[tuple[str, str, tuple[str, ...], float]] = []

    def evaluate(
        self,
        *,
        proposal: str,
        reference: str,
        active_memories: tuple[str, ...],
        minimum_confidence: float,
    ) -> AgentProposalEvaluation:
        self.calls.append((proposal, reference, active_memories, minimum_confidence))
        return self._result


class FailOnceAfterAppendMemory(RecordingMemoryStore):
    def __init__(self) -> None:
        super().__init__()
        self._armed = False

    def arm_failure(self) -> None:
        self._armed = True

    def append(self, text: str, *, idempotency_key: str) -> MemoryAppendResult:
        result = super().append(text, idempotency_key=idempotency_key)
        if self._armed:
            self._armed = False
            raise MemoryStoreError("injected boundary failure")
        return result


class SensitivityMutatingEvaluator(RecordingEvaluator):
    def __init__(self, engine: Engine) -> None:
        super().__init__()
        self._engine = engine

    def evaluate(
        self,
        *,
        proposal: str,
        reference: str,
        active_memories: tuple[str, ...],
        minimum_confidence: float,
    ) -> AgentProposalEvaluation:
        result = super().evaluate(
            proposal=proposal,
            reference=reference,
            active_memories=active_memories,
            minimum_confidence=minimum_confidence,
        )
        with session_scope(self._engine) as database_session:
            event = database_session.get(EventModel, str(EVENT_ID))
            assert event is not None
            event.sensitivity = Sensitivity.SENSITIVE.value
        return result


def _engine(tmp_path: Path) -> Engine:
    database_path = tmp_path / "contx.db"
    upgrade_database(database_path)
    return create_database_engine(database_path)


def _service(
    engine: Engine,
    memory: RecordingMemoryStore,
    evaluator: RecordingEvaluator,
) -> AgentProposalAdoptionService:
    return AgentProposalAdoptionService(
        engine=engine,
        memory_store=memory,
        evaluator=evaluator,
        clock=FixedClock(NOW + timedelta(minutes=1)),
    )


def _submit(
    engine: Engine,
    *,
    reference_type: ProposalReferenceType = ProposalReferenceType.EVENT,
    reference_id: UUID = EVENT_ID,
) -> AgentProposal:
    return AgentProposalService(
        engine=engine,
        clock=FixedClock(NOW),
        identifiers=SequenceIdentifiers((PROPOSAL_ID,)),
    ).submit(
        agent_id="codex",
        agent_role=AgentRole.PRIMARY,
        text=PROPOSAL_TEXT,
        reference_type=reference_type,
        reference_id=reference_id,
    )


def _seed_event(engine: Engine) -> None:
    observation = Observation(
        id=OBSERVATION_ID,
        idempotency_key="d" * 64,
        source_type=SourceType.SYNTHETIC,
        captured_at=NOW,
        started_at=NOW,
        ended_at=NOW + timedelta(minutes=10),
        expires_at=NOW + timedelta(hours=48),
        created_at=NOW,
    )
    event = Event(
        id=EVENT_ID,
        idempotency_key="e" * 64,
        lineage_key="f" * 64,
        type=EventType.PROJECT_WORK,
        summary="Atlas deploys locally from the verified workspace.",
        facts={"deployment": "local"},
        started_at=NOW,
        ended_at=NOW + timedelta(minutes=10),
        valid_from=NOW,
        valid_until=NOW + timedelta(minutes=10),
        epistemic_status=EpistemicStatus.OBSERVED,
        confidence=0.95,
        sensitivity=Sensitivity.PERSONAL,
        projects=("Atlas",),
        source_observation_ids=(observation.id,),
        processing_version="session-events-v1",
        created_at=NOW,
        updated_at=NOW,
    )
    with session_scope(engine) as database_session:
        repository = PipelineRepository(database_session)
        repository.save_observation(observation)
        repository.save_event(event)


def _seed_existing_memory(
    engine: Engine,
    memory: RecordingMemoryStore,
) -> MemoryLink:
    candidate = MemoryCandidate(
        id=EXISTING_CANDIDATE_ID,
        idempotency_key="1" * 64,
        text="Atlas uses Python.",
        source_type="event",
        source_ids=(EVENT_ID,),
        utility=0.8,
        importance=0.8,
        durability=0.9,
        novelty=0.9,
        confidence=0.9,
        sensitivity=Sensitivity.PERSONAL,
        score=0.8,
        status=CandidateStatus.STORED,
        created_at=NOW,
        processed_at=NOW,
    )
    memory.initialize()
    appended = memory.append(candidate.text, idempotency_key=candidate.idempotency_key)
    link = MemoryLink(
        id=EXISTING_MEMORY_ID,
        memory_backend_id=appended.backend_id,
        candidate_id=candidate.id,
        provenance=MemoryProvenance(
            candidate_id=candidate.id,
            event_ids=(EVENT_ID,),
            observation_ids=(OBSERVATION_ID,),
        ),
        confidence=candidate.confidence,
        created_at=NOW,
    )
    with session_scope(engine) as database_session:
        repository = PipelineRepository(database_session)
        repository.save_candidate(candidate)
        repository.save_memory_link(link)
    return link


def _seed_pattern(engine: Engine) -> Pattern:
    observation = Observation(
        id=SECOND_OBSERVATION_ID,
        idempotency_key="2" * 64,
        source_type=SourceType.SYNTHETIC,
        captured_at=NOW,
        started_at=NOW,
        ended_at=NOW + timedelta(minutes=10),
        expires_at=NOW + timedelta(hours=48),
        created_at=NOW,
    )
    event = Event(
        id=SECOND_EVENT_ID,
        idempotency_key="3" * 64,
        lineage_key="4" * 64,
        type=EventType.PROJECT_WORK,
        summary="Atlas local deployment was verified again.",
        facts={"deployment": "local"},
        started_at=NOW,
        ended_at=NOW + timedelta(minutes=10),
        valid_from=NOW,
        valid_until=NOW + timedelta(minutes=10),
        epistemic_status=EpistemicStatus.OBSERVED,
        confidence=0.9,
        sensitivity=Sensitivity.PERSONAL,
        projects=("Atlas",),
        source_observation_ids=(observation.id,),
        processing_version="session-events-v1",
        created_at=NOW,
        updated_at=NOW,
    )
    run = ProcessingRun(
        id=PATTERN_RUN_ID,
        pipeline="patterns",
        version="pattern-v1",
        started_at=NOW,
        ended_at=NOW,
        status=ProcessingRunStatus.SUCCEEDED,
        input_count=2,
        output_count=1,
    )
    pattern = Pattern(
        id=PATTERN_ID,
        idempotency_key="5" * 64,
        type=PatternType.PROJECT_RECURRENCE,
        summary="Atlas local deployment recurs across verified work.",
        window_start=NOW - timedelta(minutes=10),
        window_end=NOW,
        epistemic_status=EpistemicStatus.INFERRED,
        confidence=0.85,
        sensitivity=Sensitivity.PERSONAL,
        evidence_count=2,
        source_event_ids=(EVENT_ID, SECOND_EVENT_ID),
        projects=("Atlas",),
        metrics={"events": 2},
        valid_from=NOW,
        valid_until=NOW + timedelta(days=1),
        processing_version="pattern-v1",
        created_at=NOW,
    )
    with session_scope(engine) as database_session:
        pipeline = PipelineRepository(database_session)
        pipeline.save_observation(observation)
        pipeline.save_event(event)
        pipeline.save_processing_run(run)
        PatternRepository(database_session).save(
            pattern,
            processing_run_id=run.id,
        )
    return pattern
