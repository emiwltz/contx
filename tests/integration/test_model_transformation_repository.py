"""Persistent local-model queue, provenance, and replay integration tests."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import Engine

from contx.db import create_database_engine, session_scope, upgrade_database
from contx.db.repositories import ModelTransformationRepository, PipelineRepository
from contx.errors import DatabaseError
from contx.model_provider import (
    LocalModelExecution,
    LocalModelRuntimeStatus,
    ModelInterpretation,
    ModelTransformation,
    ModelTransformationStatus,
)
from contx.models import (
    Observation,
    ObservationStatus,
    ProcessingRun,
    Sensitivity,
    SourceType,
)

NOW = datetime(2026, 8, 2, 18, 0, tzinfo=UTC)
OBSERVATION_ID = UUID("00000000-0000-0000-0000-000000000011")
TRANSFORMATION_ID = UUID("00000000-0000-0000-0000-000000000012")
RUN_ID = UUID("00000000-0000-0000-0000-000000000013")
RETRY_RUN_ID = UUID("00000000-0000-0000-0000-000000000014")
MODEL = "qwen3-vl:4b-instruct-q4_K_M"
DIGEST = f"sha256:{'a' * 64}"
IMAGE_HASH = "c" * 64


def test_transformation_round_trips_with_queryable_provenance(tmp_path: Path) -> None:
    engine = _database_engine(tmp_path)
    pending = _pending()
    running = pending.start(runtime=_runtime(), started_at=NOW)
    succeeded = running.succeed(_execution())
    try:
        with session_scope(engine) as session:
            pipeline = PipelineRepository(session)
            pipeline.save_observation(_screenshot())
            pipeline.save_processing_run(_run())
            repository = ModelTransformationRepository(session)
            assert repository.save(pending) == pending
            assert repository.due(at=NOW) == (pending,)
            repository.save(running, processing_run_id=RUN_ID)
            repository.save(succeeded, processing_run_id=RUN_ID)
            repository.save(succeeded, processing_run_id=RUN_ID)

        with session_scope(engine) as session:
            repository = ModelTransformationRepository(session)
            assert repository.count() == 1
            assert repository.by_id(TRANSFORMATION_ID) == succeeded
            assert repository.by_idempotency_key("a" * 64) == succeeded
            assert repository.processing_run_ids(TRANSFORMATION_ID) == (RUN_ID,)
            assert repository.due(at=NOW + timedelta(days=1)) == ()
    finally:
        engine.dispose()


def test_failed_transformation_is_due_then_links_a_second_attempt(
    tmp_path: Path,
) -> None:
    engine = _database_engine(tmp_path)
    retry_at = NOW + timedelta(minutes=1)
    pending = _pending()
    running = pending.start(runtime=_runtime(), started_at=NOW)
    failed = running.fail(
        ended_at=NOW + timedelta(seconds=1),
        error_code="runtime_unavailable",
        next_attempt_at=retry_at,
    )
    retried = failed.start(runtime=_runtime(), started_at=retry_at)
    try:
        with session_scope(engine) as session:
            pipeline = PipelineRepository(session)
            pipeline.save_observation(_screenshot())
            pipeline.save_processing_run(_run())
            pipeline.save_processing_run(_run(run_id=RETRY_RUN_ID, started_at=retry_at))
            repository = ModelTransformationRepository(session)
            repository.save(pending)
            repository.save(running, processing_run_id=RUN_ID)
            repository.save(failed, processing_run_id=RUN_ID)
            assert repository.due(at=retry_at - timedelta(microseconds=1)) == ()
            assert repository.due(at=retry_at) == (failed,)
            repository.save(retried, processing_run_id=RETRY_RUN_ID)

        with session_scope(engine) as session:
            repository = ModelTransformationRepository(session)
            persisted = repository.by_id(TRANSFORMATION_ID)
            assert persisted is not None
            assert persisted.status is ModelTransformationStatus.RUNNING
            assert persisted.attempt_count == 2
            assert repository.processing_run_ids(TRANSFORMATION_ID) == (
                RUN_ID,
                RETRY_RUN_ID,
            )
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("missing", "source observation is missing"),
        ("rejected", "excluded or unavailable source"),
        ("wrong_hash", "matching screenshot"),
    ],
)
def test_invalid_source_provenance_is_rejected(
    tmp_path: Path,
    case: str,
    message: str,
) -> None:
    engine = _database_engine(tmp_path)
    observation = (
        None
        if case == "missing"
        else _screenshot(
            **(
                {"processing_status": ObservationStatus.REJECTED}
                if case == "rejected"
                else {}
            )
        )
    )
    transformation = _pending(
        **({"image_sha256": "d" * 64} if case == "wrong_hash" else {})
    )
    try:
        with (
            pytest.raises(DatabaseError, match=message),
            session_scope(engine) as session,
        ):
            if observation is not None:
                PipelineRepository(session).save_observation(observation)
            ModelTransformationRepository(session).save(transformation)
    finally:
        engine.dispose()


def test_repository_rejects_skipped_lifecycle_and_conflicting_replay(
    tmp_path: Path,
) -> None:
    engine = _database_engine(tmp_path)
    pending = _pending()
    running = pending.start(runtime=_runtime(), started_at=NOW)
    succeeded = running.succeed(_execution())
    conflict = _pending(
        id=UUID("00000000-0000-0000-0000-000000000099"),
        configured_model="other-local-model",
    )
    try:
        with session_scope(engine) as session:
            pipeline = PipelineRepository(session)
            pipeline.save_observation(_screenshot())
            pipeline.save_processing_run(_run())
            repository = ModelTransformationRepository(session)
            repository.save(pending)
            with pytest.raises(DatabaseError, match="state transition"):
                repository.save(succeeded, processing_run_id=RUN_ID)
            with pytest.raises(DatabaseError, match="idempotency key conflicts"):
                repository.save(conflict)
    finally:
        engine.dispose()


def _database_engine(tmp_path: Path) -> Engine:
    database_path = tmp_path / "contx.db"
    upgrade_database(database_path)
    return create_database_engine(database_path)


def _screenshot(**overrides: object) -> Observation:
    values: dict[str, object] = {
        "id": OBSERVATION_ID,
        "idempotency_key": "b" * 64,
        "source_type": SourceType.SCREENSHOT,
        "captured_at": NOW,
        "started_at": NOW,
        "ended_at": NOW,
        "app_name": "Synthetic Editor",
        "app_bundle_id": "com.example.editor",
        "window_title": "CONTX persistence",
        "artifact_path": "/synthetic/raw/screenshot.png",
        "content_hash": IMAGE_HASH,
        "expires_at": NOW + timedelta(hours=48),
        "created_at": NOW,
    }
    values.update(overrides)
    return Observation.model_validate(values)


def _pending(**overrides: object) -> ModelTransformation:
    values: dict[str, object] = {
        "id": TRANSFORMATION_ID,
        "idempotency_key": "a" * 64,
        "source_observation_ids": (OBSERVATION_ID,),
        "endpoint": "http://127.0.0.1:11434",
        "configured_model": MODEL,
        "image_sha256": IMAGE_HASH,
        "next_attempt_at": NOW,
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(overrides)
    return ModelTransformation.model_validate(values)


def _runtime() -> LocalModelRuntimeStatus:
    return LocalModelRuntimeStatus(
        endpoint="http://127.0.0.1:11434",
        runtime_available=True,
        runtime_version="0.32.5",
        model=MODEL,
        model_available=True,
        model_digest=DIGEST,
    )


def _execution() -> LocalModelExecution:
    return LocalModelExecution(
        request_id=TRANSFORMATION_ID,
        source_observation_ids=(OBSERVATION_ID,),
        endpoint="http://127.0.0.1:11434",
        runtime_version="0.32.5",
        model=MODEL,
        model_digest=DIGEST,
        image_sha256=IMAGE_HASH,
        interpretation=ModelInterpretation(
            summary="Editing CONTX persistence.",
            activity_type="coding",
            observed_facts=("A source file and tests are visible.",),
            inferred_context=("The CONTX project is being developed.",),
            projects=("CONTX",),
            entities=("Ollama",),
            sensitivity=Sensitivity.PERSONAL,
            sensitive_categories=(),
            confidence=0.9,
            memory_relevance=0.8,
        ),
        started_at=NOW,
        ended_at=NOW + timedelta(seconds=1),
        wall_duration_ms=700,
        runtime_duration_ms=650,
        load_duration_ms=20,
        prompt_eval_count=100,
        eval_count=40,
    )


def _run(
    *,
    run_id: UUID = RUN_ID,
    started_at: datetime = NOW,
) -> ProcessingRun:
    return ProcessingRun(
        id=run_id,
        pipeline="local_model",
        version="local-screen-v1",
        started_at=started_at,
    )
