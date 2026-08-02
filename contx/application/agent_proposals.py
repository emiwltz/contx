"""Validated agent proposal inbox with no direct final-memory write path."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import Engine

from contx.db import session_scope
from contx.db.repositories import (
    AgentProposalRepository,
    PatternRepository,
    PipelineRepository,
)
from contx.errors import PipelineError
from contx.models import (
    AgentProposal,
    AgentProposalStatus,
    AgentProposalType,
    AgentRole,
    Clock,
    IdentifierSource,
    MemoryLinkStatus,
    PatternStatus,
    ProposalReferenceType,
)
from contx.models.common import build_idempotency_key

OPTMEM_PROPOSAL_BYTES = 280


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
