"""Append-only event correction persistence and lineage tests."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import Engine

from contx.db import create_database_engine, session_scope, upgrade_database
from contx.db.repositories import EventCorrectionRepository, PipelineRepository
from contx.errors import DatabaseError
from contx.models import (
    EpistemicStatus,
    Event,
    EventCorrection,
    EventCorrectionContent,
    EventType,
    Observation,
    Sensitivity,
    SourceType,
)

NOW = datetime(2026, 8, 2, 10, 0, tzinfo=UTC)
OBSERVATION_ID = UUID("00000000-0000-0000-0000-000000000801")
EVENT_ID = UUID("00000000-0000-0000-0000-000000000802")
FIRST_CORRECTION_ID = UUID("00000000-0000-0000-0000-000000000803")
SECOND_CORRECTION_ID = UUID("00000000-0000-0000-0000-000000000804")
LINEAGE_KEY = "c" * 64


def test_corrections_form_an_idempotent_append_only_lineage(tmp_path: Path) -> None:
    engine = _database_engine(tmp_path)
    first = _correction(
        correction_id=FIRST_CORRECTION_ID,
        idempotency_key="d" * 64,
        summary="Corrected CONTX activity.",
    )
    second = _correction(
        correction_id=SECOND_CORRECTION_ID,
        idempotency_key="e" * 64,
        summary="Final corrected CONTX activity.",
        supersedes_correction_id=FIRST_CORRECTION_ID,
    )
    try:
        with session_scope(engine) as session:
            pipeline = PipelineRepository(session)
            pipeline.save_observation(_observation())
            pipeline.save_event(_event())
            repository = EventCorrectionRepository(session)
            assert repository.save(first) == first
            assert repository.save(first) == first

        with session_scope(engine) as session:
            repository = EventCorrectionRepository(session)
            assert repository.save(second) == second
            assert repository.latest_for_lineages((LINEAGE_KEY,)) == {
                LINEAGE_KEY: second
            }
            assert repository.by_id(FIRST_CORRECTION_ID) == first
            assert repository.by_id(SECOND_CORRECTION_ID) == second
    finally:
        engine.dispose()


def test_correction_rejects_missing_target_wrong_lineage_and_chain_fork(
    tmp_path: Path,
) -> None:
    engine = _database_engine(tmp_path)
    try:
        with session_scope(engine) as session:
            pipeline = PipelineRepository(session)
            pipeline.save_observation(_observation())
            pipeline.save_event(_event())
            repository = EventCorrectionRepository(session)
            repository.save(
                _correction(
                    correction_id=FIRST_CORRECTION_ID,
                    idempotency_key="d" * 64,
                    summary="Corrected CONTX activity.",
                )
            )

        with (
            pytest.raises(DatabaseError, match="latest lineage"),
            session_scope(engine) as session,
        ):
            EventCorrectionRepository(session).save(
                _correction(
                    correction_id=SECOND_CORRECTION_ID,
                    idempotency_key="e" * 64,
                    summary="Forked correction.",
                )
            )

        with (
            pytest.raises(DatabaseError, match="lineage"),
            session_scope(engine) as session,
        ):
            EventCorrectionRepository(session).save(
                _correction(
                    correction_id=SECOND_CORRECTION_ID,
                    idempotency_key="f" * 64,
                    summary="Wrong-lineage correction.",
                    event_lineage_key="f" * 64,
                    supersedes_correction_id=FIRST_CORRECTION_ID,
                )
            )

        with (
            pytest.raises(DatabaseError, match="missing event"),
            session_scope(engine) as session,
        ):
            EventCorrectionRepository(session).save(
                _correction(
                    correction_id=SECOND_CORRECTION_ID,
                    idempotency_key="0" * 64,
                    summary="Missing-target correction.",
                    target_event_id=UUID("00000000-0000-0000-0000-000000000899"),
                    supersedes_correction_id=FIRST_CORRECTION_ID,
                )
            )
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
        app_bundle_id="dev.contx.synthetic",
        expires_at=NOW + timedelta(hours=48),
        created_at=NOW,
    )


def _event() -> Event:
    return Event(
        id=EVENT_ID,
        idempotency_key="b" * 64,
        lineage_key=LINEAGE_KEY,
        type=EventType.PROJECT_WORK,
        summary="Initial CONTX activity.",
        facts={"active_seconds": 1200},
        started_at=NOW,
        ended_at=NOW + timedelta(minutes=20),
        valid_from=NOW,
        valid_until=NOW + timedelta(minutes=20),
        epistemic_status=EpistemicStatus.INFERRED,
        confidence=0.8,
        sensitivity=Sensitivity.PERSONAL,
        projects=("CONTX",),
        source_observation_ids=(OBSERVATION_ID,),
        processing_version="session-events-v1",
        created_at=NOW,
        updated_at=NOW,
    )


def _correction(
    *,
    correction_id: UUID,
    idempotency_key: str,
    summary: str,
    event_lineage_key: str = LINEAGE_KEY,
    target_event_id: UUID = EVENT_ID,
    supersedes_correction_id: UUID | None = None,
) -> EventCorrection:
    return EventCorrection(
        id=correction_id,
        idempotency_key=idempotency_key,
        event_lineage_key=event_lineage_key,
        target_event_id=target_event_id,
        replacement=EventCorrectionContent(
            type=EventType.PROJECT_WORK,
            summary=summary,
            epistemic_status=EpistemicStatus.OBSERVED,
            confidence=1.0,
            projects=("CONTX",),
            valid_from=NOW,
            valid_until=NOW + timedelta(minutes=20),
        ),
        reason="User corrected the synthetic event.",
        supersedes_correction_id=supersedes_correction_id,
        created_at=NOW,
    )
