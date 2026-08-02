"""Restart-safe orchestration for mandatory local-model interpretation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import ValidationError
from sqlalchemy import Engine

from contx.db import session_scope
from contx.db.repositories import ModelTransformationRepository, PipelineRepository
from contx.errors import (
    ContxError,
    DatabaseError,
    LocalModelNotInstalledError,
    LocalModelProtocolError,
    LocalModelResponseError,
    LocalModelUnavailableError,
    PipelineError,
    RawStoreError,
)
from contx.model_provider import (
    OUTPUT_SCHEMA_VERSION,
    PROMPT_VERSION,
    LocalModelRequest,
    LocalModelRuntimeStatus,
    LoopbackHttpEndpoint,
    ModelAttempt,
    ModelAttemptInvocation,
    ModelProvider,
    ModelTransformation,
    ModelTransformationStatus,
)
from contx.models import (
    Clock,
    IdentifierSource,
    Observation,
    ObservationStatus,
    ProcessingRun,
    ProcessingRunStatus,
    SourceType,
)
from contx.models.common import build_idempotency_key
from contx.raw_store import RawStore

LOCAL_MODEL_PIPELINE_VERSION = "local-model-processing-v1"
DEFAULT_BATCH_SIZE = 10
DEFAULT_QUEUE_LIMIT = 1000
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_RETRY_BASE = timedelta(seconds=30)
DEFAULT_RETRY_MAX = timedelta(minutes=15)
DEFAULT_STALE_AFTER = timedelta(minutes=10)


@dataclass(frozen=True, slots=True)
class LocalModelProcessingResult:
    """Content-free audit summary for one bounded processing pass."""

    run: ProcessingRun
    runtime: LocalModelRuntimeStatus
    queued_transformation_ids: tuple[UUID, ...]
    succeeded_transformation_ids: tuple[UUID, ...]
    failed_transformation_ids: tuple[UUID, ...]
    abandoned_transformation_ids: tuple[UUID, ...]
    recovered_transformation_ids: tuple[UUID, ...]
    backlog_count: int
    total_abandoned_count: int

    @property
    def succeeded(self) -> bool:
        return self.run.status is ProcessingRunStatus.SUCCEEDED


@dataclass(frozen=True, slots=True)
class _AttemptOutcome:
    transformation_id: UUID
    status: ModelTransformationStatus


class LocalModelProcessingService:
    """Queue and interpret authorized screenshots through one local provider."""

    def __init__(
        self,
        *,
        engine: Engine,
        raw_store: RawStore,
        provider: ModelProvider,
        endpoint: str,
        configured_model: str,
        max_image_bytes: int,
        clock: Clock,
        identifiers: IdentifierSource,
        batch_size: int = DEFAULT_BATCH_SIZE,
        queue_limit: int = DEFAULT_QUEUE_LIMIT,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        retry_base: timedelta = DEFAULT_RETRY_BASE,
        retry_max: timedelta = DEFAULT_RETRY_MAX,
        stale_after: timedelta = DEFAULT_STALE_AFTER,
    ) -> None:
        if batch_size < 1:
            raise ValueError("local model batch size must be positive")
        if queue_limit < batch_size:
            raise ValueError("local model queue limit must cover one batch")
        if not 1 <= max_attempts <= 10:
            raise ValueError("local model attempts must be between one and ten")
        if retry_base <= timedelta(0) or retry_max < retry_base:
            raise ValueError("local model retry bounds are invalid")
        if stale_after < timedelta(minutes=1):
            raise ValueError("local model stale timeout must be at least one minute")
        if not 1024 <= max_image_bytes <= 50 * 1024 * 1024:
            raise ValueError("local model image limit is outside safe bounds")
        if not configured_model.strip() or len(configured_model) > 255:
            raise ValueError("configured local model name is invalid")
        self._engine = engine
        self._raw_store = raw_store
        self._provider = provider
        self._endpoint = LoopbackHttpEndpoint.parse(endpoint).url
        self._configured_model = configured_model
        self._max_image_bytes = max_image_bytes
        self._clock = clock
        self._identifiers = identifiers
        self._batch_size = batch_size
        self._queue_limit = queue_limit
        self._max_attempts = max_attempts
        self._retry_base = retry_base
        self._retry_max = retry_max
        self._stale_after = stale_after

    def run_once(self) -> LocalModelProcessingResult:
        """Run one bounded pass without retaining model input in logs or results."""
        started_at = self._clock.now()
        running_run = ProcessingRun(
            id=self._identifiers.new(),
            pipeline="local_model",
            version=LOCAL_MODEL_PIPELINE_VERSION,
            started_at=started_at,
        )
        self._save_run(running_run)
        attempted_count = 0
        output_count = 0
        try:
            recovered = self._recover_stale(at=started_at)
            queued = self._queue_new_screenshots(at=started_at)
            due = self._due(at=started_at)
            runtime = self._provider.status()
            runtime_error = self._runtime_error_code(runtime)
            if runtime_error is not None:
                run = running_run.fail(
                    ended_at=self._clock.now(),
                    error_code=runtime_error,
                    input_count=len(due),
                    output_count=0,
                )
                self._save_run(run)
                backlog, abandoned = self._queue_counts(at=self._clock.now())
                return LocalModelProcessingResult(
                    run=run,
                    runtime=runtime,
                    queued_transformation_ids=queued,
                    succeeded_transformation_ids=(),
                    failed_transformation_ids=(),
                    abandoned_transformation_ids=(),
                    recovered_transformation_ids=recovered,
                    backlog_count=backlog,
                    total_abandoned_count=abandoned,
                )

            collected_outcomes: list[_AttemptOutcome] = []
            for transformation in due:
                attempted_count += 1
                outcome = self._attempt(
                    transformation,
                    runtime=runtime,
                    run_id=running_run.id,
                )
                collected_outcomes.append(outcome)
                if outcome.status is ModelTransformationStatus.SUCCEEDED:
                    output_count += 1
            outcomes = tuple(collected_outcomes)
            succeeded = tuple(
                outcome.transformation_id
                for outcome in outcomes
                if outcome.status is ModelTransformationStatus.SUCCEEDED
            )
            failed = tuple(
                outcome.transformation_id
                for outcome in outcomes
                if outcome.status is ModelTransformationStatus.FAILED
            )
            abandoned_ids = tuple(
                outcome.transformation_id
                for outcome in outcomes
                if outcome.status is ModelTransformationStatus.ABANDONED
            )
            ended_at = self._clock.now()
            run = (
                running_run.succeed(
                    ended_at=ended_at,
                    input_count=len(outcomes),
                    output_count=len(succeeded),
                )
                if not failed and not abandoned_ids
                else running_run.fail(
                    ended_at=ended_at,
                    error_code="local_model_partial_failure",
                    input_count=len(outcomes),
                    output_count=len(succeeded),
                )
            )
            self._save_run(run)
            backlog, abandoned = self._queue_counts(at=self._clock.now())
            return LocalModelProcessingResult(
                run=run,
                runtime=runtime,
                queued_transformation_ids=queued,
                succeeded_transformation_ids=succeeded,
                failed_transformation_ids=failed,
                abandoned_transformation_ids=abandoned_ids,
                recovered_transformation_ids=recovered,
                backlog_count=backlog,
                total_abandoned_count=abandoned,
            )
        except Exception as error:
            failed_run = running_run.fail(
                ended_at=self._clock.now(),
                error_code=_safe_model_processing_error_code(error),
                input_count=attempted_count,
                output_count=output_count,
            )
            try:
                self._save_run(failed_run)
            except Exception:
                raise PipelineError(
                    "Local model processing failed and its status could not be recorded"
                ) from error
            if isinstance(error, ContxError):
                raise
            raise PipelineError("Local model processing failed") from error

    def _queue_new_screenshots(self, *, at: datetime) -> tuple[UUID, ...]:
        with session_scope(self._engine) as session:
            repository = ModelTransformationRepository(session)
            observations = repository.unqueued_screenshots(
                at=at,
                limit=self._queue_limit,
                provider="ollama",
                endpoint=self._endpoint,
                configured_model=self._configured_model,
                prompt_version=PROMPT_VERSION,
                output_schema_version=OUTPUT_SCHEMA_VERSION,
            )
            records = tuple(
                self._pending(observation, created_at=at)
                for observation in observations
            )
            return tuple(repository.save(record).id for record in records)

    def _pending(
        self,
        observation: Observation,
        *,
        created_at: datetime,
    ) -> ModelTransformation:
        if observation.content_hash is None:
            raise DatabaseError("A screenshot observation is missing its content hash")
        key = build_idempotency_key(
            "local-model-transformation-v1",
            observation.id,
            observation.content_hash,
            "ollama",
            self._endpoint,
            self._configured_model,
            PROMPT_VERSION,
            OUTPUT_SCHEMA_VERSION,
        )
        return ModelTransformation(
            id=uuid5(NAMESPACE_URL, f"contx:model-transformation:{key}"),
            idempotency_key=key,
            source_observation_ids=(observation.id,),
            endpoint=self._endpoint,
            configured_model=self._configured_model,
            image_sha256=observation.content_hash,
            next_attempt_at=created_at,
            created_at=created_at,
            updated_at=created_at,
        )

    def _due(self, *, at: datetime) -> tuple[ModelTransformation, ...]:
        with session_scope(self._engine) as session:
            return ModelTransformationRepository(session).due(
                at=at,
                limit=self._batch_size,
                provider="ollama",
                endpoint=self._endpoint,
                configured_model=self._configured_model,
                prompt_version=PROMPT_VERSION,
                output_schema_version=OUTPUT_SCHEMA_VERSION,
            )

    def _attempt(
        self,
        transformation: ModelTransformation,
        *,
        runtime: LocalModelRuntimeStatus,
        run_id: UUID,
    ) -> _AttemptOutcome:
        attempt_started_at = self._clock.now()
        if (
            transformation.model_digest is not None
            and runtime.model_digest != transformation.model_digest
        ):
            abandoned = transformation.abandon(
                ended_at=attempt_started_at,
                error_code="model_digest_changed",
            )
            self._save_transformation(abandoned, run_id=run_id)
            return _AttemptOutcome(
                transformation_id=abandoned.id,
                status=abandoned.status,
            )
        running = transformation.start(
            runtime=runtime,
            started_at=attempt_started_at,
        )
        self._save_transformation(running, run_id=run_id)
        invocation = ModelAttemptInvocation.NOT_INVOKED
        try:
            request = self._request(running)
            invocation = ModelAttemptInvocation.INVOKED
            execution = self._provider.interpret(request)
            try:
                succeeded = running.succeed(execution)
            except ValueError as error:
                raise LocalModelProtocolError(
                    "Local model execution provenance did not match its request"
                ) from error
            self._persist_success(succeeded, run_id=run_id)
            return _AttemptOutcome(
                transformation_id=succeeded.id,
                status=succeeded.status,
            )
        except ContxError as error:
            failed = self._record_attempt_failure(
                running,
                error=error,
                run_id=run_id,
                invocation=invocation,
            )
            return _AttemptOutcome(
                transformation_id=failed.id,
                status=failed.status,
            )

    def _request(self, transformation: ModelTransformation) -> LocalModelRequest:
        with session_scope(self._engine) as session:
            observations = ModelTransformationRepository(session).source_observations(
                transformation
            )
        if len(observations) != 1:
            raise DatabaseError("Local model work requires exactly one screenshot")
        observation = observations[0]
        if (
            observation.source_type is not SourceType.SCREENSHOT
            or observation.excluded
            or observation.processing_status
            in {ObservationStatus.REJECTED, ObservationStatus.PURGED}
            or observation.artifact_path is None
            or observation.content_hash != transformation.image_sha256
        ):
            raise RawStoreError("Model source observation is unavailable")
        if observation.expires_at <= self._clock.now():
            raise RawStoreError("Model source observation has expired")
        payload = self._raw_store.read(
            Path(observation.artifact_path),
            expected_sha256=transformation.image_sha256,
            max_bytes=self._max_image_bytes,
        )
        try:
            return LocalModelRequest(
                id=transformation.id,
                source_observation_ids=transformation.source_observation_ids,
                captured_at=observation.captured_at,
                started_at=observation.started_at,
                ended_at=observation.ended_at,
                activity_state=observation.activity_state,
                app_name=observation.app_name,
                app_bundle_id=observation.app_bundle_id,
                window_title=observation.window_title,
                image_sha256=transformation.image_sha256,
                image_bytes=payload,
            )
        except ValidationError as error:
            raise RawStoreError("Model source observation is invalid") from error

    def _persist_success(
        self,
        transformation: ModelTransformation,
        *,
        run_id: UUID,
    ) -> None:
        with session_scope(self._engine) as session:
            model_repository = ModelTransformationRepository(session)
            model_repository.save(transformation, processing_run_id=run_id)
            model_repository.save_attempt(
                _terminal_attempt(
                    transformation,
                    run_id=run_id,
                    invocation=ModelAttemptInvocation.INVOKED,
                )
            )
            pipeline_repository = PipelineRepository(session)
            for observation in model_repository.source_observations(transformation):
                if observation.processing_status is not ObservationStatus.COLLECTED:
                    continue
                processed = Observation.model_validate(
                    observation.model_dump()
                    | {"processing_status": ObservationStatus.PROCESSED}
                )
                pipeline_repository.save_observation(processed)

    def _record_attempt_failure(
        self,
        running: ModelTransformation,
        *,
        error: ContxError,
        run_id: UUID,
        invocation: ModelAttemptInvocation,
    ) -> ModelTransformation:
        ended_at = self._clock.now()
        code = _safe_model_processing_error_code(error)
        terminal = isinstance(error, (RawStoreError, DatabaseError))
        if terminal or running.attempt_count >= self._max_attempts:
            record = running.abandon(ended_at=ended_at, error_code=code)
        else:
            record = running.fail(
                ended_at=ended_at,
                error_code=code,
                next_attempt_at=ended_at + self._retry_delay(running.attempt_count),
            )
        with session_scope(self._engine) as session:
            repository = ModelTransformationRepository(session)
            repository.save(record, processing_run_id=run_id)
            repository.save_attempt(
                _terminal_attempt(
                    record,
                    run_id=run_id,
                    invocation=invocation,
                )
            )
        return record

    def _retry_delay(self, attempt_count: int) -> timedelta:
        multiplier = 2 ** max(0, attempt_count - 1)
        scaled = timedelta(
            seconds=self._retry_base.total_seconds() * multiplier,
        )
        return min(scaled, self._retry_max)

    def _recover_stale(self, *, at: datetime) -> tuple[UUID, ...]:
        with session_scope(self._engine) as session:
            repository = ModelTransformationRepository(session)
            pipeline = PipelineRepository(session)
            stale = repository.stale_running(
                before=at - self._stale_after,
                limit=self._queue_limit,
            )
            for transformation in stale:
                run_ids = repository.processing_run_ids(transformation.id)
                if not run_ids:
                    raise DatabaseError(
                        "A running model transformation has no processing run"
                    )
                recovered = (
                    transformation.abandon(
                        ended_at=at,
                        error_code="interrupted_model_attempt",
                    )
                    if transformation.attempt_count >= self._max_attempts
                    else transformation.fail(
                        ended_at=at,
                        error_code="interrupted_model_attempt",
                        next_attempt_at=at,
                    )
                )
                repository.save(recovered, processing_run_id=run_ids[-1])
                repository.save_attempt(
                    _terminal_attempt(
                        recovered,
                        run_id=run_ids[-1],
                        invocation=ModelAttemptInvocation.UNKNOWN,
                    )
                )
                for run_id in run_ids:
                    run = pipeline.processing_run_by_id(run_id)
                    if run is None:
                        raise DatabaseError(
                            "A model transformation references a missing processing run"
                        )
                    if run.status is ProcessingRunStatus.RUNNING:
                        pipeline.save_processing_run(
                            run.fail(
                                ended_at=at,
                                error_code="interrupted_model_attempt",
                                input_count=max(1, run.input_count),
                                output_count=run.output_count,
                            )
                        )
            return tuple(record.id for record in stale)

    def _save_transformation(
        self,
        transformation: ModelTransformation,
        *,
        run_id: UUID,
    ) -> None:
        with session_scope(self._engine) as session:
            ModelTransformationRepository(session).save(
                transformation,
                processing_run_id=run_id,
            )

    def _save_run(self, run: ProcessingRun) -> None:
        with session_scope(self._engine) as session:
            PipelineRepository(session).save_processing_run(run)

    def _queue_counts(self, *, at: datetime) -> tuple[int, int]:
        with session_scope(self._engine) as session:
            repository = ModelTransformationRepository(session)
            configuration = {
                "provider": "ollama",
                "endpoint": self._endpoint,
                "configured_model": self._configured_model,
                "prompt_version": PROMPT_VERSION,
                "output_schema_version": OUTPUT_SCHEMA_VERSION,
            }
            backlog = repository.backlog_count(
                **configuration
            ) + repository.unqueued_screenshot_count(
                at=at,
                **configuration,
            )
            return backlog, repository.abandoned_count()

    def _runtime_error_code(self, runtime: LocalModelRuntimeStatus) -> str | None:
        if (
            runtime.endpoint != self._endpoint
            or runtime.model != self._configured_model
        ):
            return "model_identity_mismatch"
        if not runtime.runtime_available:
            return "runtime_unavailable"
        if not runtime.model_available:
            return "model_not_installed"
        return None


def _safe_model_processing_error_code(error: Exception) -> str:
    if isinstance(error, LocalModelNotInstalledError):
        return "model_not_installed"
    if isinstance(error, LocalModelUnavailableError):
        return "runtime_unavailable"
    if isinstance(error, LocalModelProtocolError):
        return "model_protocol_error"
    if isinstance(error, LocalModelResponseError):
        return "invalid_model_response"
    if isinstance(error, RawStoreError):
        return "raw_artifact_unavailable"
    if isinstance(error, DatabaseError):
        return "database_error"
    if isinstance(error, PipelineError):
        return "pipeline_error"
    return "unexpected_model_processing_failure"


def _terminal_attempt(
    transformation: ModelTransformation,
    *,
    run_id: UUID,
    invocation: ModelAttemptInvocation,
) -> ModelAttempt:
    if transformation.started_at is None or transformation.ended_at is None:
        raise DatabaseError("Terminal model transformation lacks attempt timestamps")
    return ModelAttempt(
        transformation_id=transformation.id,
        processing_run_id=run_id,
        attempt_number=transformation.attempt_count,
        invocation=invocation,
        status=transformation.status,
        error_code=transformation.last_error_code,
        started_at=transformation.started_at,
        ended_at=transformation.ended_at,
    )
