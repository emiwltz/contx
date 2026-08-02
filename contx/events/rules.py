"""Narrow deterministic event rules for the synthetic vertical slice."""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta

from contx.models import (
    Clock,
    EpistemicStatus,
    Event,
    IdentifierSource,
    Observation,
    Sensitivity,
)
from contx.models.common import build_idempotency_key

EVENT_PROCESSING_VERSION = "synthetic-events-v1"
CONTX_SYNTHETIC_BUNDLE = "dev.contx.synthetic-editor"
RESUMPTION_GAP = timedelta(hours=24)
MINIMUM_RESUMPTION_ACTIVITY_SECONDS = 30 * 60


class VerticalSliceEventBuilder:
    """Detect one evidenced project resumption and bounded trivial activity."""

    def __init__(self, *, clock: Clock, identifiers: IdentifierSource) -> None:
        self._clock = clock
        self._identifiers = identifiers

    def build(self, observations: tuple[Observation, ...]) -> tuple[Event, ...]:
        grouped: dict[str, list[Observation]] = defaultdict(list)
        for observation in observations:
            if not observation.excluded and observation.app_bundle_id:
                grouped[observation.app_bundle_id].append(observation)

        events: list[Event] = []
        for bundle_id, group in sorted(grouped.items()):
            ordered = sorted(
                group, key=lambda item: item.started_at or item.captured_at
            )
            if bundle_id == CONTX_SYNTHETIC_BUNDLE:
                resumption = self._build_resumption(ordered)
                if resumption is not None:
                    events.append(resumption)
            else:
                events.append(self._build_brief_activity(ordered))
        return tuple(events)

    def _build_resumption(self, observations: list[Observation]) -> Event | None:
        gap_index: int | None = None
        gap_seconds = 0
        for index in range(1, len(observations)):
            previous_end = observations[index - 1].ended_at
            current_start = observations[index].started_at
            if previous_end is None or current_start is None:
                continue
            gap = current_start - previous_end
            if gap >= RESUMPTION_GAP:
                gap_index = index
                gap_seconds = int(gap.total_seconds())
        if gap_index is None:
            return None

        current = observations[gap_index:]
        active_seconds = sum(_duration_seconds(item) for item in current)
        if active_seconds < MINIMUM_RESUMPTION_ACTIVITY_SECONDS:
            return None

        source_ids = tuple(item.id for item in observations)
        started_at = current[0].started_at or current[0].captured_at
        ended_at = current[-1].ended_at or current[-1].captured_at
        now = self._clock.now()
        return Event(
            id=self._identifiers.new(),
            idempotency_key=build_idempotency_key(
                "project-resumption-event-v1",
                EVENT_PROCESSING_VERSION,
                tuple(item.idempotency_key for item in observations),
            ),
            type="project_resumption",
            summary="Resumed sustained work on CONTX after a multi-day gap.",
            facts={
                "app_bundle_id": CONTX_SYNTHETIC_BUNDLE,
                "gap_seconds": gap_seconds,
                "current_active_seconds": active_seconds,
            },
            started_at=started_at,
            ended_at=ended_at,
            epistemic_status=EpistemicStatus.INFERRED,
            confidence=0.92,
            sensitivity=Sensitivity.PERSONAL,
            projects=("CONTX",),
            entities=(),
            source_observation_ids=source_ids,
            processing_version=EVENT_PROCESSING_VERSION,
            created_at=now,
            updated_at=now,
        )

    def _build_brief_activity(self, observations: list[Observation]) -> Event:
        source_ids = tuple(item.id for item in observations)
        first = observations[0]
        last = observations[-1]
        started_at = first.started_at or first.captured_at
        ended_at = last.ended_at or last.captured_at
        active_seconds = sum(_duration_seconds(item) for item in observations)
        now = self._clock.now()
        return Event(
            id=self._identifiers.new(),
            idempotency_key=build_idempotency_key(
                "brief-activity-event-v1",
                EVENT_PROCESSING_VERSION,
                tuple(item.idempotency_key for item in observations),
            ),
            type="brief_activity",
            summary="Observed one brief active-application metadata sample.",
            facts={
                "app_bundle_id": first.app_bundle_id,
                "active_seconds": active_seconds,
            },
            started_at=started_at,
            ended_at=ended_at,
            epistemic_status=EpistemicStatus.OBSERVED,
            confidence=1.0,
            sensitivity=Sensitivity.PERSONAL,
            projects=(),
            entities=(),
            source_observation_ids=source_ids,
            processing_version=EVENT_PROCESSING_VERSION,
            created_at=now,
            updated_at=now,
        )


def _duration_seconds(observation: Observation) -> int:
    if observation.started_at is None or observation.ended_at is None:
        return 0
    return max(0, int((observation.ended_at - observation.started_at).total_seconds()))
