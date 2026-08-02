"""Model transformation to durable event pipeline integration tests."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import Engine

from contx.application import LocalModelEventService
from contx.db import create_database_engine, session_scope, upgrade_database
from contx.db.models import EventModel
from contx.db.repositories import (
    ModelEventRepository,
    ModelTransformationRepository,
    PipelineRepository,
    RawObservationRepository,
)
from contx.errors import PipelineError
from contx.events import MODEL_EVENT_PROCESSING_VERSION, ModelTransformationEventBuilder
from contx.model_provider import (
    LocalModelExecution,
    LocalModelRuntimeStatus,
    ModelInterpretation,
    ModelTransformation,
)
from contx.models import (
    Event,
    Observation,
    ProcessingRun,
    ProcessingRunStatus,
    Sensitivity,
    SourceType,
)
from tests.helpers import FixedClock, SequenceIdentifiers

NOW = datetime(2026, 8, 2, 21, 0, tzinfo=UTC)
OBSERVATION_ID = UUID("00000000-0000-0000-0000-000000000701")
TRANSFORMATION_ID = UUID("00000000-0000-0000-0000-000000000702")
MODEL_RUN_ID = UUID("00000000-0000-0000-0000-000000000703")
EVENT_RUN_ID = UUID("00000000-0000-0000-0000-000000000704")
REPLAY_RUN_ID = UUID("00000000-0000-0000-0000-000000000705")
DIGEST = f"sha256:{'a' * 64}"


def test_successful_model_output_builds_one_replayable_event_with_foreign_keys(
    tmp_path: Path,
) -> None:
    engine = _database_engine(tmp_path)
    _persist_successful_transformation(engine)
    service = _service(engine, identifiers=(EVENT_RUN_ID, REPLAY_RUN_ID))
    try:
        first = service.run_once()
        replay = service.run_once()

        assert first.succeeded
        assert first.transformation_ids == (TRANSFORMATION_ID,)
        assert len(first.events) == 1
        event = first.events[0]
        assert event.type == "coding"
        assert event.processing_version == MODEL_EVENT_PROCESSING_VERSION
        assert event.summary == "Editing the CONTX model-event pipeline."
        assert first.backlog_count == 0
        assert replay.succeeded
        assert replay.events == ()
        assert replay.run.input_count == 0

        with session_scope(engine) as session:
            repository = ModelEventRepository(session)
            pipeline = PipelineRepository(session)
            assert repository.transformation_ids(event.id) == (TRANSFORMATION_ID,)
            assert repository.processing_run_ids(event.id) == (EVENT_RUN_ID,)
            assert (
                repository.pending_count(
                    processing_version=MODEL_EVENT_PROCESSING_VERSION
                )
                == 0
            )
            assert pipeline.count(EventModel) == 1
            event_run = pipeline.processing_run_by_id(EVENT_RUN_ID)
            replay_run = pipeline.processing_run_by_id(REPLAY_RUN_ID)
            assert event_run is not None
            assert event_run.status is ProcessingRunStatus.SUCCEEDED
            assert replay_run is not None
            assert replay_run.status is ProcessingRunStatus.SUCCEEDED
    finally:
        engine.dispose()


def test_event_build_survives_raw_tombstone_after_successful_interpretation(
    tmp_path: Path,
) -> None:
    engine = _database_engine(tmp_path)
    _persist_successful_transformation(engine)
    with session_scope(engine) as session:
        RawObservationRepository(session).tombstone(OBSERVATION_ID)
    try:
        result = _service(engine, identifiers=(EVENT_RUN_ID,)).run_once()

        assert result.succeeded
        assert len(result.events) == 1
        assert result.events[0].source_observation_ids == (OBSERVATION_ID,)
    finally:
        engine.dispose()


def test_builder_failure_rolls_back_events_and_records_content_safe_run(
    tmp_path: Path,
) -> None:
    engine = _database_engine(tmp_path)
    _persist_successful_transformation(engine)
    private_message = "synthetic-private-model-event-text"

    class FailingBuilder(ModelTransformationEventBuilder):
        def build(
            self,
            transformation: ModelTransformation,
            observations: tuple[Observation, ...],
        ) -> Event:
            raise RuntimeError(private_message)

    service = LocalModelEventService(
        engine=engine,
        builder=FailingBuilder(clock=FixedClock(NOW)),
        clock=FixedClock(NOW),
        identifiers=SequenceIdentifiers((EVENT_RUN_ID,)),
    )
    try:
        with pytest.raises(PipelineError) as caught:
            service.run_once()

        assert private_message not in str(caught.value)
        with session_scope(engine) as session:
            pipeline = PipelineRepository(session)
            run = pipeline.processing_run_by_id(EVENT_RUN_ID)
            assert run is not None
            assert run.status is ProcessingRunStatus.FAILED
            assert run.error_code == "unexpected_model_event_failure"
            assert pipeline.count(EventModel) == 0
    finally:
        engine.dispose()


def _service(
    engine: Engine,
    *,
    identifiers: tuple[UUID, ...],
) -> LocalModelEventService:
    clock = FixedClock(NOW)
    return LocalModelEventService(
        engine=engine,
        builder=ModelTransformationEventBuilder(clock=clock),
        clock=clock,
        identifiers=SequenceIdentifiers(identifiers),
    )


def _persist_successful_transformation(engine: Engine) -> None:
    observation = _observation()
    pending = _pending()
    running = pending.start(runtime=_runtime(), started_at=NOW)
    succeeded = running.succeed(_execution())
    model_run = ProcessingRun(
        id=MODEL_RUN_ID,
        pipeline="local_model",
        version="local-model-processing-v1",
        started_at=NOW,
    ).succeed(ended_at=NOW, input_count=1, output_count=1)
    with session_scope(engine) as session:
        pipeline = PipelineRepository(session)
        pipeline.save_observation(observation)
        pipeline.save_processing_run(model_run)
        repository = ModelTransformationRepository(session)
        repository.save(pending)
        repository.save(running, processing_run_id=MODEL_RUN_ID)
        repository.save(succeeded, processing_run_id=MODEL_RUN_ID)


def _database_engine(tmp_path: Path) -> Engine:
    database_path = tmp_path / "contx.db"
    upgrade_database(database_path)
    return create_database_engine(database_path)


def _observation() -> Observation:
    return Observation(
        id=OBSERVATION_ID,
        idempotency_key="b" * 64,
        source_type=SourceType.SCREENSHOT,
        captured_at=NOW,
        started_at=NOW,
        ended_at=NOW + timedelta(seconds=1),
        app_name="Synthetic Editor",
        app_bundle_id="com.example.editor",
        window_title="CONTX model-event pipeline",
        artifact_path="/synthetic/screenshot.png",
        content_hash="c" * 64,
        expires_at=NOW + timedelta(hours=48),
        created_at=NOW,
    )


def _pending() -> ModelTransformation:
    return ModelTransformation(
        id=TRANSFORMATION_ID,
        idempotency_key="a" * 64,
        source_observation_ids=(OBSERVATION_ID,),
        endpoint="http://127.0.0.1:11434",
        configured_model="qwen3-vl:4b-instruct-q4_K_M",
        image_sha256="c" * 64,
        next_attempt_at=NOW,
        created_at=NOW,
        updated_at=NOW,
    )


def _runtime() -> LocalModelRuntimeStatus:
    return LocalModelRuntimeStatus(
        endpoint="http://127.0.0.1:11434",
        runtime_available=True,
        runtime_version="0.32.5",
        model="qwen3-vl:4b-instruct-q4_K_M",
        model_available=True,
        model_digest=DIGEST,
    )


def _execution() -> LocalModelExecution:
    return LocalModelExecution(
        request_id=TRANSFORMATION_ID,
        source_observation_ids=(OBSERVATION_ID,),
        endpoint="http://127.0.0.1:11434",
        runtime_version="0.32.5",
        model="qwen3-vl:4b-instruct-q4_K_M",
        model_digest=DIGEST,
        image_sha256="c" * 64,
        interpretation=ModelInterpretation(
            summary="Editing the CONTX model-event pipeline.",
            activity_type="coding",
            observed_facts=("Source code and tests are visible.",),
            inferred_context=("The model-event pipeline is under development.",),
            projects=("CONTX",),
            entities=("Ollama",),
            sensitivity=Sensitivity.PERSONAL,
            sensitive_categories=(),
            confidence=0.9,
            memory_relevance=0.8,
        ),
        started_at=NOW,
        ended_at=NOW + timedelta(seconds=1),
        wall_duration_ms=1000,
    )
