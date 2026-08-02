"""Restart-safe construction of events from successful model transformations."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import Engine

from contx.db import session_scope
from contx.db.repositories import (
    ModelEventRepository,
    ModelTransformationRepository,
    PipelineRepository,
)
from contx.errors import ContxError, DatabaseError, PipelineError
from contx.events import (
    MODEL_EVENT_PROCESSING_VERSION,
    ModelTransformationEventBuilder,
)
from contx.model_provider import ModelTransformation
from contx.models import (
    Clock,
    Event,
    IdentifierSource,
    ProcessingRun,
    ProcessingRunStatus,
)

DEFAULT_MODEL_EVENT_BATCH_SIZE = 100


@dataclass(frozen=True, slots=True)
class LocalModelEventResult:
    """Content-safe outcome for one deterministic event-building pass."""

    run: ProcessingRun
    transformation_ids: tuple[UUID, ...]
    events: tuple[Event, ...]
    backlog_count: int

    @property
    def succeeded(self) -> bool:
        return self.run.status is ProcessingRunStatus.SUCCEEDED


class LocalModelEventService:
    """Build replayable events only from persisted successful interpretations."""

    def __init__(
        self,
        *,
        engine: Engine,
        builder: ModelTransformationEventBuilder,
        clock: Clock,
        identifiers: IdentifierSource,
        batch_size: int = DEFAULT_MODEL_EVENT_BATCH_SIZE,
    ) -> None:
        if batch_size < 1:
            raise ValueError("model event batch size must be positive")
        self._engine = engine
        self._builder = builder
        self._clock = clock
        self._identifiers = identifiers
        self._batch_size = batch_size

    def run_once(self) -> LocalModelEventResult:
        running_run = ProcessingRun(
            id=self._identifiers.new(),
            pipeline="model_events",
            version=MODEL_EVENT_PROCESSING_VERSION,
            started_at=self._clock.now(),
        )
        self._save_run(running_run)
        transformations: tuple[ModelTransformation, ...] = ()
        events: tuple[Event, ...] = ()
        try:
            with session_scope(self._engine) as session:
                event_repository = ModelEventRepository(session)
                model_repository = ModelTransformationRepository(session)
                transformations = event_repository.pending_transformations(
                    processing_version=MODEL_EVENT_PROCESSING_VERSION,
                    limit=self._batch_size,
                )
                events = tuple(
                    self._builder.build(
                        transformation,
                        model_repository.source_observations(transformation),
                    )
                    for transformation in transformations
                )

            succeeded_run = running_run.succeed(
                ended_at=self._clock.now(),
                input_count=len(transformations),
                output_count=len(events),
            )
            with session_scope(self._engine) as session:
                pipeline = PipelineRepository(session)
                pipeline.save_processing_run(succeeded_run)
                repository = ModelEventRepository(session)
                persisted = tuple(
                    repository.save(
                        event,
                        transformation_ids=(transformation.id,),
                        processing_run_id=succeeded_run.id,
                    )
                    for transformation, event in zip(
                        transformations,
                        events,
                        strict=True,
                    )
                )
            backlog = self._backlog_count()
            return LocalModelEventResult(
                run=succeeded_run,
                transformation_ids=tuple(
                    transformation.id for transformation in transformations
                ),
                events=persisted,
                backlog_count=backlog,
            )
        except Exception as error:
            failed_run = running_run.fail(
                ended_at=self._clock.now(),
                error_code=_safe_model_event_error_code(error),
                input_count=len(transformations),
                output_count=0,
            )
            try:
                self._save_run(failed_run)
            except Exception:
                raise PipelineError(
                    "Model event building failed and its status could not be recorded"
                ) from error
            if isinstance(error, ContxError):
                raise
            raise PipelineError("Model event building failed") from error

    def _save_run(self, run: ProcessingRun) -> None:
        with session_scope(self._engine) as session:
            PipelineRepository(session).save_processing_run(run)

    def _backlog_count(self) -> int:
        with session_scope(self._engine) as session:
            return ModelEventRepository(session).pending_count(
                processing_version=MODEL_EVENT_PROCESSING_VERSION
            )


def _safe_model_event_error_code(error: Exception) -> str:
    if isinstance(error, DatabaseError):
        return "database_error"
    if isinstance(error, PipelineError):
        return "pipeline_error"
    return "unexpected_model_event_failure"
