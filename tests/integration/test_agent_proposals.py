"""Agent proposals remain provenance-gated and outside final memory."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from sqlalchemy import Engine

from contx.application import AgentProposalService
from contx.db import create_database_engine, session_scope, upgrade_database
from contx.db.repositories import AgentProposalRepository, PipelineRepository
from contx.models import (
    AgentProposalStatus,
    AgentRole,
    EpistemicStatus,
    Event,
    EventType,
    Observation,
    ProposalReferenceType,
    Sensitivity,
    SourceType,
)
from tests.helpers import FixedClock, SequenceIdentifiers

NOW = datetime(2026, 8, 2, 21, 0, tzinfo=UTC)
OBSERVATION_ID = UUID("a0000000-0000-0000-0000-000000000001")
EVENT_ID = UUID("a0000000-0000-0000-0000-000000000002")
SENSITIVE_EVENT_ID = UUID("a0000000-0000-0000-0000-000000000003")
PROPOSAL_IDS = tuple(
    UUID(f"a0000000-0000-0000-0000-{index:012d}") for index in range(10, 20)
)


def test_agent_proposals_are_validated_without_memory_append(tmp_path: Path) -> None:
    database_path = tmp_path / "contx.db"
    upgrade_database(database_path)
    engine = create_database_engine(database_path)
    _persist_references(engine)
    service = AgentProposalService(
        engine=engine,
        clock=FixedClock(NOW),
        identifiers=SequenceIdentifiers(PROPOSAL_IDS),
    )
    try:
        valid = service.submit(
            agent_id="codex",
            agent_role=AgentRole.PRIMARY,
            text="Atlas work should resume from the verified event state.",
            reference_type=ProposalReferenceType.EVENT,
            reference_id=EVENT_ID,
        )
        replay = service.submit(
            agent_id="codex",
            agent_role=AgentRole.PRIMARY,
            text="Atlas work should resume from the verified event state.",
            reference_type=ProposalReferenceType.EVENT,
            reference_id=EVENT_ID,
        )
        no_reference = service.submit(
            agent_id="codex",
            agent_role=AgentRole.PRIMARY,
            text="An unsupported conclusion should not reach final memory.",
        )
        subagent = service.submit(
            agent_id="codex-subagent",
            agent_role=AgentRole.SUBAGENT,
            text="A subagent cannot submit final memory.",
            reference_type=ProposalReferenceType.EVENT,
            reference_id=EVENT_ID,
        )
        missing = service.submit(
            agent_id="codex",
            agent_role=AgentRole.PRIMARY,
            text="A missing reference cannot support this proposal.",
            reference_type=ProposalReferenceType.EVENT,
            reference_id=UUID("a0000000-0000-0000-0000-000000000099"),
        )
        sensitive = service.submit(
            agent_id="codex",
            agent_role=AgentRole.PRIMARY,
            text="Sensitive evidence must not become durable memory.",
            reference_type=ProposalReferenceType.EVENT,
            reference_id=SENSITIVE_EVENT_ID,
        )

        assert valid.status is AgentProposalStatus.PENDING
        assert replay == valid
        assert no_reference.status is AgentProposalStatus.DEFERRED
        assert no_reference.reason == "provenance_reference_required"
        assert subagent.status is AgentProposalStatus.REJECTED
        assert subagent.reason == "subagent_proposals_require_primary_adoption"
        assert missing.status is AgentProposalStatus.REJECTED
        assert missing.reason == "reference_not_found"
        assert sensitive.status is AgentProposalStatus.REJECTED
        assert sensitive.reason == "reference_sensitivity_not_eligible"

        with session_scope(engine) as database_session:
            proposals = AgentProposalRepository(database_session).list()
            assert len(proposals) == 5
            assert AgentProposalRepository(database_session).list(
                status=AgentProposalStatus.PENDING
            ) == (valid,)
    finally:
        engine.dispose()


def _persist_references(engine: Engine) -> None:
    observation = Observation(
        id=OBSERVATION_ID,
        idempotency_key="a" * 64,
        source_type=SourceType.SYNTHETIC,
        captured_at=NOW,
        started_at=NOW,
        ended_at=NOW + timedelta(minutes=10),
        expires_at=NOW + timedelta(hours=48),
        created_at=NOW,
    )
    events = (
        _event(EVENT_ID, observation, sensitivity=Sensitivity.PERSONAL),
        _event(SENSITIVE_EVENT_ID, observation, sensitivity=Sensitivity.SENSITIVE),
    )
    with session_scope(engine) as database_session:
        repository = PipelineRepository(database_session)
        repository.save_observation(observation)
        for event in events:
            repository.save_event(event)


def _event(
    event_id: UUID,
    observation: Observation,
    *,
    sensitivity: Sensitivity,
) -> Event:
    return Event(
        id=event_id,
        idempotency_key=("b" if sensitivity is Sensitivity.PERSONAL else "c") * 64,
        lineage_key=("d" if sensitivity is Sensitivity.PERSONAL else "e") * 64,
        type=EventType.PROJECT_WORK,
        summary="Synthetic Atlas event.",
        facts={},
        started_at=NOW,
        ended_at=NOW + timedelta(minutes=10),
        valid_from=NOW,
        valid_until=NOW + timedelta(minutes=10),
        epistemic_status=EpistemicStatus.INFERRED,
        confidence=0.8,
        sensitivity=sensitivity,
        projects=("Atlas",),
        source_observation_ids=(observation.id,),
        processing_version="session-events-v1",
        created_at=NOW,
        updated_at=NOW,
    )
