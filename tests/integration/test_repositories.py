"""Repository idempotency, provenance, and transaction tests."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import Engine

from contx.db import create_database_engine, session_scope, upgrade_database
from contx.db.models import EventModel, MemoryCandidateModel, ObservationModel
from contx.db.repositories import PipelineRepository
from contx.models import (
    CandidateStatus,
    EpistemicStatus,
    Event,
    MemoryCandidate,
    MemoryLink,
    MemoryProvenance,
    Observation,
    Sensitivity,
    SourceType,
)

OBSERVATION_ID = UUID("00000000-0000-0000-0000-000000000001")
EVENT_ID = UUID("00000000-0000-0000-0000-000000000002")
CANDIDATE_ID = UUID("00000000-0000-0000-0000-000000000003")
LINK_ID = UUID("00000000-0000-0000-0000-000000000004")
NOW = datetime(2026, 8, 2, 10, 0, tzinfo=UTC)


def test_replay_is_idempotent_and_provenance_is_queryable(tmp_path: Path) -> None:
    engine = _database_engine(tmp_path)
    observation = _observation()
    event = _event()
    candidate = _candidate()
    link = _link()
    try:
        with session_scope(engine) as session:
            repository = PipelineRepository(session)
            repository.save_observation(observation)
            repository.save_event(event)
            repository.save_candidate(candidate)
            repository.save_memory_link(link)

        with session_scope(engine) as session:
            repository = PipelineRepository(session)
            repository.save_observation(observation)
            repository.save_event(event)
            repository.save_candidate(candidate)
            repository.save_memory_link(link)
            assert repository.count(ObservationModel) == 1
            assert repository.count(EventModel) == 1
            persisted_link = repository.memory_link_by_backend_id("memory-1")
            assert persisted_link is not None
            assert persisted_link.provenance.observation_ids == (OBSERVATION_ID,)
    finally:
        engine.dispose()


def test_transaction_rolls_back_on_pipeline_failure(tmp_path: Path) -> None:
    engine = _database_engine(tmp_path)
    try:
        with (
            pytest.raises(RuntimeError, match="injected"),
            session_scope(engine) as session,
        ):
            PipelineRepository(session).save_observation(_observation())
            raise RuntimeError("injected failure")

        with session_scope(engine) as session:
            assert PipelineRepository(session).count(ObservationModel) == 0
    finally:
        engine.dispose()


def test_candidate_state_transition_is_persisted(tmp_path: Path) -> None:
    engine = _database_engine(tmp_path)
    pending = MemoryCandidate.model_validate(
        _candidate().model_dump()
        | {"status": CandidateStatus.PENDING, "processed_at": None}
    )
    accepted = pending.decide(CandidateStatus.ACCEPTED, processed_at=NOW)
    try:
        with session_scope(engine) as session:
            repository = PipelineRepository(session)
            repository.save_observation(_observation())
            repository.save_event(_event())
            repository.save_candidate(pending)
            repository.save_candidate(accepted)

        with session_scope(engine) as session:
            model = session.get(MemoryCandidateModel, str(CANDIDATE_ID))
            assert model is not None
            assert model.status == CandidateStatus.ACCEPTED.value
    finally:
        engine.dispose()


def _database_engine(tmp_path: Path) -> Engine:
    database_path = tmp_path / "contx.db"
    upgrade_database(database_path)
    return create_database_engine(database_path)


def _observation() -> Observation:
    return Observation(
        id=OBSERVATION_ID,
        idempotency_key="a" * 64,
        source_type=SourceType.SYNTHETIC,
        captured_at=NOW,
        started_at=NOW,
        ended_at=NOW + timedelta(minutes=20),
        app_name="Synthetic Editor",
        app_bundle_id="test.synthetic.editor",
        expires_at=NOW + timedelta(hours=48),
        created_at=NOW,
    )


def _event() -> Event:
    return Event(
        id=EVENT_ID,
        idempotency_key="b" * 64,
        type="project_work",
        summary="Worked on CONTX persistence.",
        facts={"active_seconds": 1200},
        started_at=NOW,
        ended_at=NOW + timedelta(minutes=20),
        epistemic_status=EpistemicStatus.INFERRED,
        confidence=0.9,
        sensitivity=Sensitivity.PERSONAL,
        projects=("CONTX",),
        source_observation_ids=(OBSERVATION_ID,),
        processing_version="event-v1",
        created_at=NOW,
        updated_at=NOW,
    )


def _candidate() -> MemoryCandidate:
    return MemoryCandidate(
        id=CANDIDATE_ID,
        idempotency_key="c" * 64,
        text="Resume CONTX from the persistence foundation.",
        source_type="event",
        source_ids=(EVENT_ID,),
        importance=0.9,
        durability=0.8,
        novelty=0.8,
        confidence=0.9,
        sensitivity=Sensitivity.PERSONAL,
        score=0.86,
        status=CandidateStatus.ACCEPTED,
        created_at=NOW,
        processed_at=NOW,
    )


def _link() -> MemoryLink:
    return MemoryLink(
        id=LINK_ID,
        memory_backend_id="memory-1",
        candidate_id=CANDIDATE_ID,
        provenance=MemoryProvenance(
            candidate_id=CANDIDATE_ID,
            event_ids=(EVENT_ID,),
            observation_ids=(OBSERVATION_ID,),
        ),
        confidence=0.9,
        created_at=NOW,
    )
