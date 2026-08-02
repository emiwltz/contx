"""Restartable expiry purge, tombstone, and failure audit tests."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from sqlalchemy import Engine, select

from contx.application import RawPurgeResult, RawPurgeService
from contx.db import create_database_engine, session_scope, upgrade_database
from contx.db.models import ObservationModel, ProcessingRunModel
from contx.db.repositories import PipelineRepository
from contx.models import (
    Observation,
    ObservationStatus,
    ProcessingRunStatus,
    SourceType,
)
from contx.raw_store import FilesystemRawStore
from tests.helpers import FixedClock, SequenceIdentifiers

NOW = datetime(2026, 8, 4, 13, 0, tzinfo=UTC)
CAPTURED = NOW - timedelta(hours=49)
OBSERVATION_ID = UUID("00000000-0000-0000-0000-000000000001")
RUN_ID = UUID("00000000-0000-0000-0000-000000000002")


def test_expired_artifact_is_deleted_and_observation_is_tombstoned(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path)
    store = FilesystemRawStore(tmp_path / "raw", disk_budget_bytes=1024)
    artifact = store.write(
        b"synthetic-private-pixels",
        artifact_id=OBSERVATION_ID,
        suffix=".png",
        captured_at=CAPTURED,
        retention=timedelta(hours=48),
    )
    _save_observation(engine, _observation(artifact_path=str(artifact.path)))
    try:
        result = _purge(engine, store)

        assert result.succeeded
        assert result.purged_observation_ids == (OBSERVATION_ID,)
        assert result.bytes_reclaimed == len(b"synthetic-private-pixels")
        assert not artifact.path.exists()
        with session_scope(engine) as session:
            model = session.get(ObservationModel, str(OBSERVATION_ID))
            assert model is not None
            assert model.processing_status == ObservationStatus.PURGED.value
            assert model.app_name is None
            assert model.app_bundle_id is None
            assert model.window_title is None
            assert model.artifact_path is None
            run = session.scalars(select(ProcessingRunModel)).one()
            assert run.status == ProcessingRunStatus.SUCCEEDED.value
    finally:
        engine.dispose()


def test_missing_file_after_interruption_is_safe_to_retry(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    store = FilesystemRawStore(tmp_path / "raw", disk_budget_bytes=1024)
    artifact = store.write(
        b"synthetic-private-pixels",
        artifact_id=OBSERVATION_ID,
        suffix=".png",
        captured_at=CAPTURED,
        retention=timedelta(hours=48),
    )
    _save_observation(engine, _observation(artifact_path=str(artifact.path)))
    artifact.path.unlink()
    try:
        result = _purge(engine, store)

        assert result.succeeded
        assert result.bytes_reclaimed == 0
        with session_scope(engine) as session:
            model = session.get(ObservationModel, str(OBSERVATION_ID))
            assert model is not None
            assert model.processing_status == ObservationStatus.PURGED.value
    finally:
        engine.dispose()


def test_unreferenced_artifact_after_crash_is_removed_on_next_purge(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path)
    store = FilesystemRawStore(tmp_path / "raw", disk_budget_bytes=1024)
    orphan = store.write(
        b"synthetic-orphaned-pixels",
        artifact_id=OBSERVATION_ID,
        suffix=".png",
        captured_at=CAPTURED,
        retention=timedelta(hours=48),
    )
    try:
        result = _purge(engine, store)

        assert result.succeeded
        assert result.orphan_artifacts_deleted == 1
        assert result.bytes_reclaimed == len(b"synthetic-orphaned-pixels")
        assert not orphan.path.exists()
    finally:
        engine.dispose()


def test_explicit_immediate_purge_removes_unexpired_raw_data(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path)
    store = FilesystemRawStore(tmp_path / "raw", disk_budget_bytes=1024)
    captured = NOW - timedelta(hours=1)
    artifact = store.write(
        b"synthetic-unexpired-pixels",
        artifact_id=OBSERVATION_ID,
        suffix=".png",
        captured_at=captured,
        retention=timedelta(hours=48),
    )
    observation = _observation(artifact_path=str(artifact.path)).model_copy(
        update={
            "captured_at": captured,
            "started_at": captured,
            "ended_at": captured,
            "expires_at": captured + timedelta(hours=48),
            "created_at": captured,
        }
    )
    _save_observation(engine, observation)
    try:
        result = RawPurgeService(
            engine=engine,
            raw_store=store,
            clock=FixedClock(NOW),
            identifiers=SequenceIdentifiers((RUN_ID,)),
        ).run(include_unexpired=True)

        assert result.succeeded
        assert result.purged_observation_ids == (OBSERVATION_ID,)
        assert not artifact.path.exists()
        with session_scope(engine) as session:
            run = session.scalars(select(ProcessingRunModel)).one()
            assert run.pipeline == "raw_purge_immediate"
    finally:
        engine.dispose()


def test_unsafe_artifact_path_fails_closed_and_is_audited(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    store = FilesystemRawStore(tmp_path / "raw", disk_budget_bytes=1024)
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"must-remain")
    _save_observation(engine, _observation(artifact_path=str(outside)))
    try:
        result = _purge(engine, store)

        assert not result.succeeded
        assert result.failed_observation_ids == (OBSERVATION_ID,)
        assert outside.read_bytes() == b"must-remain"
        with session_scope(engine) as session:
            observation = session.get(ObservationModel, str(OBSERVATION_ID))
            assert observation is not None
            assert observation.processing_status == ObservationStatus.PROCESSED.value
            run = session.scalars(select(ProcessingRunModel)).one()
            assert run.status == ProcessingRunStatus.FAILED.value
            assert run.error_code == "raw_purge_partial_failure"
            assert run.error_summary is None
    finally:
        engine.dispose()


def _observation(*, artifact_path: str) -> Observation:
    return Observation(
        id=OBSERVATION_ID,
        idempotency_key="a" * 64,
        source_type=SourceType.ACTIVE_APP,
        captured_at=CAPTURED,
        started_at=CAPTURED,
        ended_at=CAPTURED,
        app_name="Synthetic Private App",
        app_bundle_id="com.example.private",
        window_title="Synthetic private title",
        artifact_path=artifact_path,
        content_hash="b" * 64,
        processing_status=ObservationStatus.PROCESSED,
        expires_at=CAPTURED + timedelta(hours=48),
        created_at=CAPTURED,
    )


def _save_observation(engine: Engine, observation: Observation) -> None:
    with session_scope(engine) as session:
        PipelineRepository(session).save_observation(observation)


def _purge(engine: Engine, store: FilesystemRawStore) -> RawPurgeResult:
    return RawPurgeService(
        engine=engine,
        raw_store=store,
        clock=FixedClock(NOW),
        identifiers=SequenceIdentifiers((RUN_ID,)),
    ).run()


def _engine(tmp_path: Path) -> Engine:
    path = tmp_path / "contx.db"
    upgrade_database(path)
    return create_database_engine(path)
