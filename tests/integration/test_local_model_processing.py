"""Synthetic end-to-end tests for the restart-safe local-model worker."""

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from sqlalchemy import Engine

from contx.application import LocalModelProcessingService
from contx.db import create_database_engine, session_scope, upgrade_database
from contx.db.repositories import ModelTransformationRepository, PipelineRepository
from contx.errors import LocalModelResponseError
from contx.model_provider import (
    LocalModelExecution,
    LocalModelRequest,
    LocalModelRuntimeStatus,
    ModelInterpretation,
    ModelTransformation,
    ModelTransformationStatus,
)
from contx.models import (
    Observation,
    ObservationStatus,
    ProcessingRun,
    ProcessingRunStatus,
    Sensitivity,
    SourceType,
)
from contx.raw_store import FilesystemRawStore
from tests.helpers import SequenceIdentifiers

NOW = datetime(2026, 8, 2, 18, 0, tzinfo=UTC)
OBSERVATION_ID = UUID("00000000-0000-0000-0000-000000000101")
OLD_RUN_ID = UUID("00000000-0000-0000-0000-000000000102")
RUN_IDS = tuple(UUID(int=value) for value in range(201, 210))
MODEL = "qwen3-vl:4b-instruct-q4_K_M"
DIGEST = f"sha256:{'a' * 64}"
OTHER_DIGEST = f"sha256:{'b' * 64}"
PNG = b"\x89PNG\r\n\x1a\nsynthetic-local-processing-fixture"


class MutableClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def now(self) -> datetime:
        return self.value


class SyntheticModelProvider:
    def __init__(
        self,
        *,
        clock: MutableClock,
        runtime: LocalModelRuntimeStatus | None = None,
        outcomes: Iterable[Exception | None] = (),
    ) -> None:
        self.clock = clock
        self.runtime = runtime or _healthy_runtime()
        self.outcomes = iter(outcomes)
        self.status_calls = 0
        self.requests: list[LocalModelRequest] = []

    def status(self) -> LocalModelRuntimeStatus:
        self.status_calls += 1
        return self.runtime

    def interpret(self, request: LocalModelRequest) -> LocalModelExecution:
        self.requests.append(request)
        outcome = next(self.outcomes, None)
        if outcome is not None:
            raise outcome
        return LocalModelExecution(
            request_id=request.id,
            source_observation_ids=request.source_observation_ids,
            endpoint=self.runtime.endpoint,
            runtime_version=self.runtime.runtime_version or "missing-runtime-version",
            model=self.runtime.model,
            model_digest=self.runtime.model_digest or "missing-model-digest",
            image_sha256=request.image_sha256,
            interpretation=_interpretation(),
            started_at=self.clock.now(),
            ended_at=self.clock.now(),
            wall_duration_ms=0,
            runtime_duration_ms=0,
            load_duration_ms=0,
            prompt_eval_count=100,
            eval_count=40,
        )


def test_worker_queues_interprets_persists_and_marks_screenshot_processed(
    tmp_path: Path,
) -> None:
    engine = _database_engine(tmp_path)
    clock = MutableClock(NOW)
    store, observation = _stored_screenshot(tmp_path)
    provider = SyntheticModelProvider(clock=clock)
    _persist_observation(engine, observation)
    try:
        result = _service(
            engine,
            store,
            provider,
            clock,
            identifiers=(RUN_IDS[0],),
        ).run_once()

        assert result.succeeded
        assert len(result.queued_transformation_ids) == 1
        assert result.succeeded_transformation_ids == (
            result.queued_transformation_ids[0],
        )
        assert result.failed_transformation_ids == ()
        assert result.backlog_count == 0
        assert provider.requests[0].window_title == "Synthetic CONTX implementation"
        assert provider.requests[0].image_bytes == PNG

        with session_scope(engine) as session:
            pipeline = PipelineRepository(session)
            persisted_observation = pipeline.observation_by_id(OBSERVATION_ID)
            persisted_run = pipeline.processing_run_by_id(RUN_IDS[0])
            transformation = ModelTransformationRepository(session).by_id(
                result.queued_transformation_ids[0]
            )
            assert persisted_observation is not None
            assert (
                persisted_observation.processing_status is ObservationStatus.PROCESSED
            )
            assert persisted_run is not None
            assert persisted_run.status is ProcessingRunStatus.SUCCEEDED
            assert transformation is not None
            assert transformation.status is ModelTransformationStatus.SUCCEEDED
            assert transformation.interpretation == _interpretation()
        assert store.size(Path(observation.artifact_path or "")) == len(PNG)
    finally:
        engine.dispose()


def test_unavailable_runtime_leaves_bounded_pending_backlog_without_model_call(
    tmp_path: Path,
) -> None:
    engine = _database_engine(tmp_path)
    clock = MutableClock(NOW)
    store, observation = _stored_screenshot(tmp_path)
    provider = SyntheticModelProvider(clock=clock, runtime=_unavailable_runtime())
    _persist_observation(engine, observation)
    try:
        result = _service(
            engine,
            store,
            provider,
            clock,
            identifiers=(RUN_IDS[0],),
        ).run_once()

        assert not result.succeeded
        assert result.run.error_code == "runtime_unavailable"
        assert result.backlog_count == 1
        assert provider.requests == []
        with session_scope(engine) as session:
            repository = ModelTransformationRepository(session)
            transformation = repository.by_id(result.queued_transformation_ids[0])
            persisted = PipelineRepository(session).observation_by_id(OBSERVATION_ID)
            assert transformation is not None
            assert transformation.status is ModelTransformationStatus.PENDING
            assert repository.processing_run_ids(transformation.id) == ()
            assert persisted is not None
            assert persisted.processing_status is ObservationStatus.COLLECTED
            assert persisted.expires_at == observation.expires_at
    finally:
        engine.dispose()


def test_invalid_response_retries_when_due_and_succeeds_idempotently(
    tmp_path: Path,
) -> None:
    engine = _database_engine(tmp_path)
    clock = MutableClock(NOW)
    store, observation = _stored_screenshot(tmp_path)
    provider = SyntheticModelProvider(
        clock=clock,
        outcomes=(LocalModelResponseError("synthetic invalid response"), None),
    )
    _persist_observation(engine, observation)
    service = _service(
        engine,
        store,
        provider,
        clock,
        identifiers=RUN_IDS[:3],
    )
    try:
        first = service.run_once()
        transformation_id = first.queued_transformation_ids[0]
        assert first.failed_transformation_ids == (transformation_id,)
        assert first.backlog_count == 1

        clock.value = NOW + timedelta(seconds=29)
        early = service.run_once()
        assert early.run.input_count == 0
        assert len(provider.requests) == 1

        clock.value = NOW + timedelta(seconds=30)
        retried = service.run_once()
        assert retried.succeeded_transformation_ids == (transformation_id,)
        assert retried.backlog_count == 0
        assert len(provider.requests) == 2

        with session_scope(engine) as session:
            repository = ModelTransformationRepository(session)
            persisted = repository.by_id(transformation_id)
            assert persisted is not None
            assert persisted.status is ModelTransformationStatus.SUCCEEDED
            assert persisted.attempt_count == 2
            assert repository.processing_run_ids(transformation_id) == (
                RUN_IDS[0],
                RUN_IDS[2],
            )
            assert repository.count() == 1
    finally:
        engine.dispose()


def test_attempt_limit_and_missing_raw_artifact_end_in_terminal_audit(
    tmp_path: Path,
) -> None:
    engine = _database_engine(tmp_path)
    clock = MutableClock(NOW)
    store, observation = _stored_screenshot(tmp_path)
    provider = SyntheticModelProvider(
        clock=clock,
        outcomes=(LocalModelResponseError("synthetic invalid response"),),
    )
    _persist_observation(engine, observation)
    try:
        result = _service(
            engine,
            store,
            provider,
            clock,
            identifiers=(RUN_IDS[0],),
            max_attempts=1,
        ).run_once()

        assert result.abandoned_transformation_ids == (
            result.queued_transformation_ids[0],
        )
        assert result.backlog_count == 0
        assert result.total_abandoned_count == 1

        second_engine = _database_engine(tmp_path / "missing")
        missing_store, missing_observation = _stored_screenshot(tmp_path / "missing")
        missing_store.delete(Path(missing_observation.artifact_path or ""))
        _persist_observation(second_engine, missing_observation)
        missing_provider = SyntheticModelProvider(clock=clock)
        try:
            missing = _service(
                second_engine,
                missing_store,
                missing_provider,
                clock,
                identifiers=(RUN_IDS[1],),
            ).run_once()
            assert len(missing.abandoned_transformation_ids) == 1
            assert missing_provider.requests == []
        finally:
            second_engine.dispose()
    finally:
        engine.dispose()


def test_excluded_screenshot_never_queues_or_reaches_the_model(tmp_path: Path) -> None:
    engine = _database_engine(tmp_path)
    clock = MutableClock(NOW)
    store, observation = _stored_screenshot(
        tmp_path,
        excluded=True,
        exclusion_reason="synthetic exclusion",
    )
    provider = SyntheticModelProvider(clock=clock)
    _persist_observation(engine, observation)
    try:
        result = _service(
            engine,
            store,
            provider,
            clock,
            identifiers=(RUN_IDS[0],),
        ).run_once()

        assert result.succeeded
        assert result.queued_transformation_ids == ()
        assert provider.requests == []
        with session_scope(engine) as session:
            assert ModelTransformationRepository(session).count() == 0
    finally:
        engine.dispose()


def test_stale_running_attempt_is_failed_audited_and_retried(tmp_path: Path) -> None:
    engine = _database_engine(tmp_path)
    old = NOW - timedelta(minutes=20)
    clock = MutableClock(NOW)
    store, observation = _stored_screenshot(tmp_path, captured_at=old)
    provider = SyntheticModelProvider(clock=clock)
    _persist_observation(engine, observation)
    pending = _pending_for(observation, at=old)
    running = pending.start(runtime=_healthy_runtime(), started_at=old)
    with session_scope(engine) as session:
        pipeline = PipelineRepository(session)
        pipeline.save_processing_run(
            ProcessingRun(
                id=OLD_RUN_ID,
                pipeline="local_model",
                version="local-model-processing-v1",
                started_at=old,
            )
        )
        repository = ModelTransformationRepository(session)
        repository.save(pending)
        repository.save(running, processing_run_id=OLD_RUN_ID)
    try:
        result = _service(
            engine,
            store,
            provider,
            clock,
            identifiers=(RUN_IDS[0],),
        ).run_once()

        assert result.recovered_transformation_ids == (running.id,)
        assert result.succeeded_transformation_ids == (running.id,)
        with session_scope(engine) as session:
            pipeline = PipelineRepository(session)
            old_run = pipeline.processing_run_by_id(OLD_RUN_ID)
            transformation = ModelTransformationRepository(session).by_id(running.id)
            assert old_run is not None
            assert old_run.status is ProcessingRunStatus.FAILED
            assert old_run.error_code == "interrupted_model_attempt"
            assert transformation is not None
            assert transformation.status is ModelTransformationStatus.SUCCEEDED
            assert transformation.attempt_count == 2
    finally:
        engine.dispose()


def test_changed_model_digest_abandons_failed_replay_without_mixing_provenance(
    tmp_path: Path,
) -> None:
    engine = _database_engine(tmp_path)
    clock = MutableClock(NOW)
    store, observation = _stored_screenshot(tmp_path)
    provider = SyntheticModelProvider(
        clock=clock,
        outcomes=(LocalModelResponseError("synthetic invalid response"),),
    )
    _persist_observation(engine, observation)
    service = _service(
        engine,
        store,
        provider,
        clock,
        identifiers=RUN_IDS[:2],
    )
    try:
        first = service.run_once()
        clock.value = NOW + timedelta(seconds=30)
        provider.runtime = _healthy_runtime(digest=OTHER_DIGEST)

        second = service.run_once()

        assert second.abandoned_transformation_ids == (
            first.queued_transformation_ids[0],
        )
        assert len(provider.requests) == 1
        with session_scope(engine) as session:
            transformation = ModelTransformationRepository(session).by_id(
                first.queued_transformation_ids[0]
            )
            assert transformation is not None
            assert transformation.status is ModelTransformationStatus.ABANDONED
            assert transformation.model_digest == DIGEST
            assert transformation.last_error_code == "model_digest_changed"
    finally:
        engine.dispose()


def _service(
    engine: Engine,
    store: FilesystemRawStore,
    provider: SyntheticModelProvider,
    clock: MutableClock,
    *,
    identifiers: Iterable[UUID],
    max_attempts: int = 3,
) -> LocalModelProcessingService:
    return LocalModelProcessingService(
        engine=engine,
        raw_store=store,
        provider=provider,
        endpoint="http://127.0.0.1:11434",
        configured_model=MODEL,
        max_image_bytes=1024,
        clock=clock,
        identifiers=SequenceIdentifiers(identifiers),
        max_attempts=max_attempts,
    )


def _stored_screenshot(
    tmp_path: Path,
    *,
    captured_at: datetime = NOW,
    **overrides: object,
) -> tuple[FilesystemRawStore, Observation]:
    store = FilesystemRawStore(tmp_path / "raw", disk_budget_bytes=1024)
    artifact = store.write(
        PNG,
        artifact_id=OBSERVATION_ID,
        suffix=".png",
        captured_at=captured_at,
        retention=timedelta(hours=48),
    )
    values: dict[str, object] = {
        "id": OBSERVATION_ID,
        "idempotency_key": "c" * 64,
        "source_type": SourceType.SCREENSHOT,
        "captured_at": captured_at,
        "started_at": captured_at,
        "ended_at": captured_at,
        "app_name": "Synthetic Editor",
        "app_bundle_id": "com.example.editor",
        "window_title": "Synthetic CONTX implementation",
        "artifact_path": str(artifact.path),
        "content_hash": artifact.content_hash,
        "expires_at": artifact.expires_at,
        "created_at": captured_at,
    }
    values.update(overrides)
    return store, Observation.model_validate(values)


def _pending_for(observation: Observation, *, at: datetime) -> ModelTransformation:
    key = _transformation_key(observation)
    return ModelTransformation(
        id=UUID("00000000-0000-0000-0000-000000000103"),
        idempotency_key=key,
        source_observation_ids=(observation.id,),
        endpoint="http://127.0.0.1:11434",
        configured_model=MODEL,
        image_sha256=observation.content_hash or "0" * 64,
        next_attempt_at=at,
        created_at=at,
        updated_at=at,
    )


def _transformation_key(observation: Observation) -> str:
    from contx.models.common import build_idempotency_key

    return build_idempotency_key(
        "local-model-transformation-v1",
        observation.id,
        observation.content_hash,
        "ollama",
        "http://127.0.0.1:11434",
        MODEL,
        "local-screen-v1",
        "model-interpretation-v1",
    )


def _persist_observation(engine: Engine, observation: Observation) -> None:
    with session_scope(engine) as session:
        PipelineRepository(session).save_observation(observation)


def _database_engine(tmp_path: Path) -> Engine:
    database_path = tmp_path / "contx.db"
    upgrade_database(database_path)
    return create_database_engine(database_path)


def _healthy_runtime(*, digest: str = DIGEST) -> LocalModelRuntimeStatus:
    return LocalModelRuntimeStatus(
        endpoint="http://127.0.0.1:11434",
        runtime_available=True,
        runtime_version="0.32.5",
        model=MODEL,
        model_available=True,
        model_digest=digest,
    )


def _unavailable_runtime() -> LocalModelRuntimeStatus:
    return LocalModelRuntimeStatus(
        endpoint="http://127.0.0.1:11434",
        runtime_available=False,
        model=MODEL,
        model_available=False,
        reason_code="runtime_unavailable",
    )


def _interpretation() -> ModelInterpretation:
    return ModelInterpretation(
        summary="Implementing the CONTX local model worker.",
        activity_type="coding",
        observed_facts=("A synthetic editor and test code are visible.",),
        inferred_context=("The CONTX local model pipeline is under test.",),
        projects=("CONTX",),
        entities=("Ollama",),
        sensitivity=Sensitivity.PERSONAL,
        sensitive_categories=(),
        confidence=0.9,
        memory_relevance=0.8,
    )
