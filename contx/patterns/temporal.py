"""Deterministic multi-event project recurrence and change rules."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from uuid import NAMESPACE_URL, uuid5

from contx.models import (
    ActivityTimeline,
    Clock,
    EpistemicStatus,
    Pattern,
    PatternType,
    Sensitivity,
    TimelineEntry,
)
from contx.models.common import build_idempotency_key, require_aware_utc

PATTERN_PROCESSING_VERSION = "temporal-patterns-v1"
DEFAULT_MIN_PROJECT_EVENTS = 2
DEFAULT_RESUMPTION_GAP = timedelta(hours=24)
DEFAULT_CHANGE_RATIO = 1.5
DEFAULT_PATTERN_VALIDITY = timedelta(days=30)


class TemporalPatternEngine:
    """Detect conservative project patterns from at least two timeline events."""

    def __init__(
        self,
        *,
        clock: Clock,
        processing_version: str = PATTERN_PROCESSING_VERSION,
        min_project_events: int = DEFAULT_MIN_PROJECT_EVENTS,
        resumption_gap: timedelta = DEFAULT_RESUMPTION_GAP,
        change_ratio: float = DEFAULT_CHANGE_RATIO,
        validity: timedelta = DEFAULT_PATTERN_VALIDITY,
    ) -> None:
        normalized = processing_version.strip()
        if (
            not normalized
            or len(normalized) > 64
            or any(character in normalized for character in "\r\n")
        ):
            raise ValueError("pattern processing version is invalid")
        if not 2 <= min_project_events <= 100:
            raise ValueError("minimum project evidence must be between 2 and 100")
        if not timedelta(hours=1) <= resumption_gap <= timedelta(days=30):
            raise ValueError("resumption gap must be between 1 hour and 30 days")
        if not 1.1 <= change_ratio <= 10.0:
            raise ValueError("change ratio must be between 1.1 and 10")
        if not timedelta(days=1) <= validity <= timedelta(days=365):
            raise ValueError("pattern validity must be between 1 and 365 days")
        self._clock = clock
        self.processing_version = normalized
        self.min_project_events = min_project_events
        self.resumption_gap = resumption_gap
        self.change_ratio = change_ratio
        self.validity = validity

    def detect(
        self,
        timeline: ActivityTimeline,
        *,
        comparison_boundary: datetime,
    ) -> tuple[Pattern, ...]:
        boundary = require_aware_utc(comparison_boundary)
        if not timeline.build.window_start < boundary < timeline.build.window_end:
            raise ValueError("comparison boundary must be inside the timeline window")
        grouped: dict[str, list[TimelineEntry]] = defaultdict(list)
        project_labels: dict[str, str] = {}
        for entry in timeline.entries:
            for project in entry.projects:
                key = project.casefold()
                project_labels.setdefault(key, project)
                grouped[key].append(entry)

        patterns: list[Pattern] = []
        for project_key in sorted(grouped):
            entries = tuple(
                sorted(
                    {entry.event_id: entry for entry in grouped[project_key]}.values(),
                    key=_entry_order,
                )
            )
            if len(entries) < self.min_project_events:
                continue
            project = project_labels[project_key]
            patterns.append(self._recurrence(project, entries, timeline))
            resumption = self._resumption(project, entries, timeline)
            if resumption is not None:
                patterns.append(resumption)
            change = self._change(project, entries, timeline, boundary)
            if change is not None:
                patterns.append(change)
        return tuple(
            sorted(
                patterns,
                key=lambda item: (
                    item.window_start,
                    item.type.value,
                    item.projects,
                    str(item.id),
                ),
            )
        )

    def _recurrence(
        self,
        project: str,
        entries: tuple[TimelineEntry, ...],
        timeline: ActivityTimeline,
    ) -> Pattern:
        active_days = len({entry.started_at.date() for entry in entries})
        return self._pattern(
            pattern_type=PatternType.PROJECT_RECURRENCE,
            project=project,
            entries=entries,
            timeline=timeline,
            summary=(
                f"Project {project} recurred across {active_days} days and "
                f"{len(entries)} activity sessions."
            ),
            epistemic_status=EpistemicStatus.INFERRED,
            confidence=_pattern_confidence(entries, evidence_weight=0.9),
            metrics={
                "active_days": active_days,
                "session_count": len(entries),
                "active_seconds": _active_seconds(entries),
            },
        )

    def _resumption(
        self,
        project: str,
        entries: tuple[TimelineEntry, ...],
        timeline: ActivityTimeline,
    ) -> Pattern | None:
        gaps = tuple(
            (
                current.started_at - previous.ended_at,
                previous,
                current,
            )
            for previous, current in zip(entries, entries[1:], strict=False)
            if current.started_at - previous.ended_at >= self.resumption_gap
        )
        if not gaps:
            return None
        gap, previous, current = max(gaps, key=lambda item: item[0])
        evidence = (previous, current)
        gap_seconds = int(gap.total_seconds())
        return self._pattern(
            pattern_type=PatternType.PROJECT_RESUMPTION,
            project=project,
            entries=evidence,
            timeline=timeline,
            summary=(
                f"Project {project} resumed after an inactivity gap of "
                f"{gap_seconds // 3600} hours."
            ),
            epistemic_status=EpistemicStatus.INFERRED,
            confidence=_pattern_confidence(evidence, evidence_weight=0.9),
            metrics={"gap_seconds": gap_seconds},
        )

    def _change(
        self,
        project: str,
        entries: tuple[TimelineEntry, ...],
        timeline: ActivityTimeline,
        boundary: datetime,
    ) -> Pattern | None:
        previous = tuple(entry for entry in entries if entry.started_at < boundary)
        current = tuple(entry for entry in entries if entry.started_at >= boundary)
        previous_seconds = _active_seconds(previous)
        current_seconds = _active_seconds(current)
        pattern_type: PatternType | None = None
        if not previous and len(current) >= self.min_project_events:
            pattern_type = PatternType.NEW_REPEATED_ACTIVITY
            summary = (
                f"Project {project} appeared repeatedly in the current comparison "
                "window after no prior activity."
            )
        elif previous_seconds > 0 and current_seconds >= (
            previous_seconds * self.change_ratio
        ):
            pattern_type = PatternType.ACTIVITY_INCREASE
            summary = (
                f"Time on project {project} increased by at least "
                f"{self.change_ratio:.1f} times between comparison windows."
            )
        elif previous_seconds > 0 and current_seconds <= (
            previous_seconds / self.change_ratio
        ):
            pattern_type = PatternType.ACTIVITY_DECREASE
            summary = (
                f"Time on project {project} decreased by at least "
                f"{self.change_ratio:.1f} times between comparison windows."
            )
        if pattern_type is None:
            return None
        return self._pattern(
            pattern_type=pattern_type,
            project=project,
            entries=entries,
            timeline=timeline,
            summary=summary,
            epistemic_status=EpistemicStatus.HYPOTHETICAL,
            confidence=_pattern_confidence(entries, evidence_weight=0.75),
            metrics={
                "previous_active_seconds": previous_seconds,
                "current_active_seconds": current_seconds,
                "change_ratio_threshold": self.change_ratio,
            },
        )

    def _pattern(
        self,
        *,
        pattern_type: PatternType,
        project: str,
        entries: tuple[TimelineEntry, ...],
        timeline: ActivityTimeline,
        summary: str,
        epistemic_status: EpistemicStatus,
        confidence: float,
        metrics: dict[str, float | int | str],
    ) -> Pattern:
        source_ids = tuple(entry.event_id for entry in entries)
        key = build_idempotency_key(
            "temporal-pattern-v1",
            self.processing_version,
            timeline.build.processing_run_id,
            pattern_type,
            project.casefold(),
            source_ids,
            metrics,
        )
        window_start = min(entry.started_at for entry in entries)
        window_end = max(entry.ended_at for entry in entries)
        return Pattern(
            id=uuid5(NAMESPACE_URL, f"contx:pattern:{key}"),
            idempotency_key=key,
            type=pattern_type,
            summary=summary,
            window_start=window_start,
            window_end=window_end,
            epistemic_status=epistemic_status,
            confidence=confidence,
            sensitivity=max(
                (entry.sensitivity for entry in entries),
                key=_sensitivity_rank,
            ),
            evidence_count=len(entries),
            source_event_ids=source_ids,
            projects=(project,),
            entities=_unique_entities(entries),
            metrics=metrics,
            valid_from=timeline.build.window_end,
            valid_until=timeline.build.window_end + self.validity,
            processing_version=self.processing_version,
            created_at=self._clock.now(),
        )


def _pattern_confidence(
    entries: tuple[TimelineEntry, ...],
    *,
    evidence_weight: float,
) -> float:
    evidence_factor = min(1.0, 0.6 + len(entries) * 0.1)
    return round(
        min(entry.confidence for entry in entries) * evidence_weight * evidence_factor,
        4,
    )


def _active_seconds(entries: tuple[TimelineEntry, ...]) -> int:
    return sum(
        max(0, int((entry.ended_at - entry.started_at).total_seconds()))
        for entry in entries
    )


def _unique_entities(entries: tuple[TimelineEntry, ...]) -> tuple[str, ...]:
    values: list[str] = []
    seen: set[str] = set()
    for entry in entries:
        for entity in entry.entities:
            key = entity.casefold()
            if key not in seen:
                seen.add(key)
                values.append(entity)
    return tuple(values)


def _sensitivity_rank(value: Sensitivity) -> int:
    return {
        Sensitivity.PUBLIC: 0,
        Sensitivity.PERSONAL: 1,
        Sensitivity.SENSITIVE: 2,
        Sensitivity.FORBIDDEN: 3,
    }[value]


def _entry_order(entry: TimelineEntry) -> tuple[datetime, datetime, str]:
    return entry.started_at, entry.ended_at, str(entry.event_id)
