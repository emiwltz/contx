"""Bounded production refresh from local observations to active memory."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from contx.application.activity_timeline import (
    ActivityTimelineBuildResult,
    ActivityTimelineService,
)
from contx.application.local_model_events import (
    LocalModelEventResult,
    LocalModelEventService,
)
from contx.application.local_model_processing import (
    LocalModelProcessingResult,
    LocalModelProcessingService,
)
from contx.application.memory_lifecycle import (
    MemoryPromotionResult,
    MemoryPromotionService,
)
from contx.application.memory_maintenance import (
    MemoryMaintenanceResult,
    MemoryMaintenanceService,
)
from contx.application.memory_projection import (
    ActiveMemoryProjectionResult,
    ActiveMemoryProjectionService,
)
from contx.application.pattern_analysis import (
    CandidateEvaluationResult,
    CandidateEvaluationService,
    PatternAnalysisResult,
    PatternAnalysisService,
    PatternCandidateResult,
    PatternCandidateService,
)
from contx.errors import PipelineError
from contx.models.common import require_aware_utc


@dataclass(frozen=True, slots=True)
class ContextRefreshResult:
    """Content-free result of one restartable production refresh attempt."""

    model_processing: LocalModelProcessingResult
    model_events: LocalModelEventResult
    timeline: ActivityTimelineBuildResult | None = None
    patterns: PatternAnalysisResult | None = None
    candidates: PatternCandidateResult | None = None
    evaluation: CandidateEvaluationResult | None = None
    promotion: MemoryPromotionResult | None = None
    maintenance: MemoryMaintenanceResult | None = None
    projection: ActiveMemoryProjectionResult | None = None
    reused_derivation: bool = False

    @property
    def blocked(self) -> bool:
        """Return whether a mandatory upstream processing stage failed."""
        return not self.model_processing.succeeded or not self.model_events.succeeded

    @property
    def deferred(self) -> bool:
        """Return whether downstream derivation waits for a drained backlog."""
        return (
            not self.blocked
            and self.projection is None
            and (
                self.model_processing.backlog_count > 0
                or self.model_events.backlog_count > 0
            )
        )

    @property
    def completed(self) -> bool:
        """Return whether every downstream stage published successfully."""
        return self.projection is not None


@dataclass(frozen=True, slots=True)
class ContextRefreshWindow:
    """One UTC-aligned rolling analysis window."""

    start: datetime
    end: datetime
    comparison_boundary: datetime


def aligned_refresh_window(
    *,
    at: datetime,
    analysis_interval: timedelta,
    analysis_window: timedelta,
    comparison_period: timedelta,
) -> ContextRefreshWindow:
    """Resolve a stable rolling window shared by periodic worker invocations."""
    try:
        current = require_aware_utc(at)
    except ValueError as error:
        raise PipelineError("Refresh timestamp must be timezone-aware") from error
    interval_seconds = int(analysis_interval.total_seconds())
    if analysis_interval != timedelta(seconds=interval_seconds) or not (
        60 <= interval_seconds <= 86400
    ):
        raise PipelineError("Refresh analysis interval is invalid")
    if not timedelta(days=2) <= analysis_window <= timedelta(days=90):
        raise PipelineError("Refresh analysis window is invalid")
    if not timedelta(days=1) <= comparison_period < analysis_window:
        raise PipelineError("Refresh comparison period is invalid")
    aligned_timestamp = int(current.timestamp()) // interval_seconds * interval_seconds
    end = datetime.fromtimestamp(aligned_timestamp, tz=UTC)
    return ContextRefreshWindow(
        start=end - analysis_window,
        end=end,
        comparison_boundary=end - comparison_period,
    )


class ContextRefreshService:
    """Drain one bounded local batch or publish one complete frozen derivation."""

    def __init__(
        self,
        *,
        model_processing: LocalModelProcessingService,
        model_events: LocalModelEventService,
        timeline: ActivityTimelineService,
        patterns: PatternAnalysisService,
        candidates: PatternCandidateService,
        evaluation: CandidateEvaluationService,
        promotion: MemoryPromotionService,
        maintenance: MemoryMaintenanceService,
        projection: ActiveMemoryProjectionService,
    ) -> None:
        self._model_processing = model_processing
        self._model_events = model_events
        self._timeline = timeline
        self._patterns = patterns
        self._candidates = candidates
        self._evaluation = evaluation
        self._promotion = promotion
        self._maintenance = maintenance
        self._projection = projection

    def run_once(
        self,
        *,
        window_start: datetime,
        window_end: datetime,
        comparison_boundary: datetime,
        reuse_completed_derivation: bool = False,
    ) -> ContextRefreshResult:
        """Process one batch and derive memory only from a fully drained queue."""
        start, end, boundary = _validate_window(
            window_start=window_start,
            window_end=window_end,
            comparison_boundary=comparison_boundary,
        )
        model_processing = self._model_processing.run_once()
        model_events = self._model_events.run_once()
        upstream = ContextRefreshResult(
            model_processing=model_processing,
            model_events=model_events,
        )
        if upstream.blocked or model_processing.backlog_count > 0:
            return upstream
        if model_events.backlog_count > 0:
            return upstream

        timeline = self._timeline.reuse_or_rebuild(
            window_start=start,
            window_end=end,
        )
        if timeline.reused and reuse_completed_derivation:
            reused = self._completed_derivation(
                timeline=timeline,
                comparison_boundary=boundary,
            )
            if reused is not None:
                patterns, candidates, evaluation, promotion = reused
                maintenance = self._maintenance.run()
                projection = self._projection.synchronize()
                return ContextRefreshResult(
                    model_processing=model_processing,
                    model_events=model_events,
                    timeline=timeline,
                    patterns=patterns,
                    candidates=candidates,
                    evaluation=evaluation,
                    promotion=promotion,
                    maintenance=maintenance,
                    projection=projection,
                    reused_derivation=True,
                )
        patterns = self._patterns.build(
            source_timeline_run_id=timeline.run.id,
            comparison_boundary=boundary,
        )
        candidates = self._candidates.build(source_pattern_run_id=patterns.run.id)
        evaluation = self._evaluation.evaluate(
            source_candidate_run_id=candidates.run.id
        )
        promotion = self._promotion.promote(source_evaluation_run_id=evaluation.run.id)
        maintenance = self._maintenance.run()
        projection = self._projection.synchronize()
        return ContextRefreshResult(
            model_processing=model_processing,
            model_events=model_events,
            timeline=timeline,
            patterns=patterns,
            candidates=candidates,
            evaluation=evaluation,
            promotion=promotion,
            maintenance=maintenance,
            projection=projection,
        )

    def _completed_derivation(
        self,
        *,
        timeline: ActivityTimelineBuildResult,
        comparison_boundary: datetime,
    ) -> (
        tuple[
            PatternAnalysisResult,
            PatternCandidateResult,
            CandidateEvaluationResult,
            MemoryPromotionResult,
        ]
        | None
    ):
        patterns = self._patterns.matching_successful(
            source_timeline_run_id=timeline.run.id,
            comparison_boundary=comparison_boundary,
        )
        if patterns is None:
            return None
        candidates = self._candidates.matching_successful(
            source_pattern_run_id=patterns.run.id
        )
        if candidates is None:
            return None
        evaluation = self._evaluation.matching_successful(
            source_candidate_run_id=candidates.run.id
        )
        if evaluation is None:
            return None
        promotion = self._promotion.matching_successful(
            source_evaluation_run_id=evaluation.run.id
        )
        if promotion is None:
            return None
        return patterns, candidates, evaluation, promotion


def _validate_window(
    *,
    window_start: datetime,
    window_end: datetime,
    comparison_boundary: datetime,
) -> tuple[datetime, datetime, datetime]:
    try:
        start = require_aware_utc(window_start)
        end = require_aware_utc(window_end)
        boundary = require_aware_utc(comparison_boundary)
    except ValueError as error:
        raise PipelineError("Refresh timestamps must be timezone-aware") from error
    if not start < boundary < end:
        raise PipelineError(
            "Refresh timestamps must satisfy start < comparison boundary < end"
        )
    return start, end, boundary
