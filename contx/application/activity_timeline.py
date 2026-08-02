"""Replayable sessionized timelines and append-only event corrections."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import Engine

from contx.db import session_scope
from contx.db.repositories import (
    EventCorrectionRepository,
    ModelEventRepository,
    ModelTransformationRepository,
    PipelineRepository,
    TimelineBuildRepository,
)
from contx.errors import ContxError, DatabaseError, PipelineError
from contx.events import (
    ModelActivitySessionizer,
    ModelEventEvidence,
    SessionizedModelEventBuilder,
)
from contx.models import (
    ActivityTimeline,
    Clock,
    Event,
    EventCorrection,
    EventCorrectionContent,
    IdentifierSource,
    ProcessingRun,
    ProcessingRunStatus,
    TimelineBuild,
    TimelineEntry,
)
from contx.models.common import build_idempotency_key

DEFAULT_TIMELINE_TRANSFORMATION_LIMIT = 10000


@dataclass(frozen=True, slots=True)
class ActivityTimelineBuildResult:
    """Content-safe result of one complete frozen-window timeline replay."""

    run: ProcessingRun
    timeline: ActivityTimeline
    event_ids: tuple[UUID, ...]
    transformation_count: int

    @property
    def succeeded(self) -> bool:
        return self.run.status is ProcessingRunStatus.SUCCEEDED


class ActivityTimelineService:
    """Rebuild and read version-selected timelines without erasing older evidence."""

    def __init__(
        self,
        *,
        engine: Engine,
        sessionizer: ModelActivitySessionizer,
        builder: SessionizedModelEventBuilder,
        clock: Clock,
        identifiers: IdentifierSource,
        transformation_limit: int = DEFAULT_TIMELINE_TRANSFORMATION_LIMIT,
    ) -> None:
        if transformation_limit < 1:
            raise ValueError("timeline transformation limit must be positive")
        self._engine = engine
        self._sessionizer = sessionizer
        self._builder = builder
        self._clock = clock
        self._identifiers = identifiers
        self._transformation_limit = transformation_limit

    def rebuild(
        self,
        *,
        window_start: datetime,
        window_end: datetime,
    ) -> ActivityTimelineBuildResult:
        run = ProcessingRun(
            id=self._identifiers.new(),
            pipeline="activity_timeline",
            version=self._builder.processing_version,
            started_at=self._clock.now(),
        )
        build = TimelineBuild(
            processing_run_id=run.id,
            processing_version=self._builder.processing_version,
            window_start=window_start,
            window_end=window_end,
            session_gap_seconds=int(self._sessionizer.session_gap.total_seconds()),
            max_session_duration_seconds=int(
                self._sessionizer.max_session_duration.total_seconds()
            ),
        )
        self._save_run_and_build(run, build)
        evidence: tuple[ModelEventEvidence, ...] = ()
        events: tuple[Event, ...] = ()
        try:
            evidence = self._window_evidence(build)
            sessions = self._sessionizer.group(evidence)
            events = tuple(self._builder.build(session) for session in sessions)
            succeeded = run.succeed(
                ended_at=self._clock.now(),
                input_count=len(evidence),
                output_count=len(events),
            )
            with session_scope(self._engine) as database_session:
                PipelineRepository(database_session).save_processing_run(succeeded)
                repository = ModelEventRepository(database_session)
                persisted = tuple(
                    repository.save(
                        event,
                        transformation_ids=session.transformation_ids,
                        processing_run_id=succeeded.id,
                    )
                    for session, event in zip(sessions, events, strict=True)
                )
        except Exception as error:
            failed = run.fail(
                ended_at=self._clock.now(),
                error_code=_safe_timeline_error_code(error),
                input_count=len(evidence),
                output_count=0,
            )
            try:
                with session_scope(self._engine) as database_session:
                    PipelineRepository(database_session).save_processing_run(failed)
            except Exception:
                raise PipelineError(
                    "Timeline replay failed and its status could not be recorded"
                ) from error
            if isinstance(error, ContxError):
                raise
            raise PipelineError("Timeline replay failed") from error
        timeline = self.read(succeeded.id)
        return ActivityTimelineBuildResult(
            run=succeeded,
            timeline=timeline,
            event_ids=tuple(event.id for event in persisted),
            transformation_count=len(evidence),
        )

    def read(self, processing_run_id: UUID) -> ActivityTimeline:
        with session_scope(self._engine) as database_session:
            pipeline = PipelineRepository(database_session)
            run = pipeline.processing_run_by_id(processing_run_id)
            build = TimelineBuildRepository(database_session).by_processing_run(
                processing_run_id
            )
            if (
                run is None
                or build is None
                or run.pipeline != "activity_timeline"
                or run.version != build.processing_version
                or run.status is not ProcessingRunStatus.SUCCEEDED
            ):
                raise PipelineError("Timeline processing run is unavailable")
            events = ModelEventRepository(database_session).events_for_processing_run(
                processing_run_id
            )
            corrections = EventCorrectionRepository(
                database_session
            ).latest_for_lineages(tuple(event.lineage_key for event in events))
            entries = tuple(
                _timeline_entry(event, corrections.get(event.lineage_key))
                for event in events
            )
        return ActivityTimeline(build=build, entries=entries)

    def _window_evidence(
        self,
        build: TimelineBuild,
    ) -> tuple[ModelEventEvidence, ...]:
        with session_scope(self._engine) as database_session:
            repository = ModelTransformationRepository(database_session)
            transformations = repository.succeeded(limit=self._transformation_limit + 1)
            if len(transformations) > self._transformation_limit:
                raise PipelineError("Timeline transformation limit was exceeded")
            evidence = tuple(
                ModelEventEvidence(
                    transformation=transformation,
                    observations=repository.source_observations(transformation),
                )
                for transformation in transformations
            )
        return tuple(
            item
            for item in evidence
            if item.started_at < build.window_end
            and item.ended_at >= build.window_start
        )

    def _save_run_and_build(
        self,
        run: ProcessingRun,
        build: TimelineBuild,
    ) -> None:
        with session_scope(self._engine) as database_session:
            PipelineRepository(database_session).save_processing_run(run)
            TimelineBuildRepository(database_session).save(build)


class EventCorrectionService:
    """Append a complete corrected semantic snapshot without mutating events."""

    def __init__(self, *, engine: Engine) -> None:
        self._engine = engine

    def correct_summary(
        self,
        *,
        event_id: UUID,
        summary: str,
        reason: str,
        correction_id: UUID,
        created_at: datetime,
    ) -> EventCorrection:
        """Append a summary correction while preserving current event semantics."""
        with session_scope(self._engine) as database_session:
            event = PipelineRepository(database_session).event_by_id(event_id)
            if event is None:
                raise PipelineError("Cannot correct a missing event")
            latest = (
                EventCorrectionRepository(database_session)
                .latest_for_lineages((event.lineage_key,))
                .get(event.lineage_key)
            )
            current = (
                correction_content(event) if latest is None else latest.replacement
            )
        try:
            replacement = EventCorrectionContent.model_validate(
                current.model_dump() | {"summary": summary}
            )
        except ValueError:
            raise PipelineError("Event correction is invalid") from None
        return self.correct(
            event_id=event_id,
            replacement=replacement,
            reason=reason,
            correction_id=correction_id,
            created_at=created_at,
        )

    def correct(
        self,
        *,
        event_id: UUID,
        replacement: EventCorrectionContent,
        reason: str,
        correction_id: UUID,
        created_at: datetime,
    ) -> EventCorrection:
        with session_scope(self._engine) as database_session:
            event = PipelineRepository(database_session).event_by_id(event_id)
            if event is None:
                raise PipelineError("Cannot correct a missing event")
            repository = EventCorrectionRepository(database_session)
            existing = repository.by_id(correction_id)
            if existing is not None:
                if (
                    existing.target_event_id != event.id
                    or existing.event_lineage_key != event.lineage_key
                    or existing.replacement != replacement
                    or existing.reason != reason.strip()
                ):
                    raise PipelineError("Event correction identity conflicts")
                return existing
            latest = repository.latest_for_lineages((event.lineage_key,)).get(
                event.lineage_key
            )
            correction = EventCorrection(
                id=correction_id,
                idempotency_key=build_idempotency_key(
                    "event-correction-v1",
                    correction_id,
                    event.lineage_key,
                    replacement.model_dump(mode="json"),
                    reason,
                ),
                event_lineage_key=event.lineage_key,
                target_event_id=event.id,
                replacement=replacement,
                reason=reason,
                supersedes_correction_id=None if latest is None else latest.id,
                created_at=created_at,
            )
            return repository.save(correction)


def correction_content(event: Event) -> EventCorrectionContent:
    """Create an editable complete correction snapshot from one base event."""
    return EventCorrectionContent(
        type=event.type,
        summary=event.summary,
        epistemic_status=event.epistemic_status,
        confidence=event.confidence,
        projects=event.projects,
        entities=event.entities,
        valid_from=event.valid_from,
        valid_until=event.valid_until,
    )


def _timeline_entry(
    event: Event,
    correction: EventCorrection | None,
) -> TimelineEntry:
    effective = (
        correction_content(event) if correction is None else correction.replacement
    )
    return TimelineEntry(
        event_id=event.id,
        correction_id=None if correction is None else correction.id,
        lineage_key=event.lineage_key,
        type=effective.type,
        summary=effective.summary,
        started_at=event.started_at,
        ended_at=event.ended_at,
        valid_from=effective.valid_from,
        valid_until=effective.valid_until,
        epistemic_status=effective.epistemic_status,
        confidence=effective.confidence,
        sensitivity=event.sensitivity,
        projects=effective.projects,
        entities=effective.entities,
        source_observation_ids=event.source_observation_ids,
        processing_version=event.processing_version,
    )


def _safe_timeline_error_code(error: Exception) -> str:
    if isinstance(error, DatabaseError):
        return "database_error"
    if isinstance(error, PipelineError):
        return "pipeline_error"
    return "unexpected_timeline_failure"
