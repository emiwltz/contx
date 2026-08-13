"""Stateful, privacy-filtered activity segmentation for the local daemon."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from contx.collection.policy import CollectionContext, CollectionPolicy
from contx.models import (
    ActivityState,
    Clock,
    CollectionControl,
    ExclusionRule,
    IdentifierSource,
    Observation,
    SourceType,
)
from contx.models.common import build_idempotency_key, require_aware_utc


@dataclass(frozen=True, slots=True)
class ActivitySample:
    """One metadata-only sample taken before any sensitive artifact capture."""

    observed_at: datetime
    activity_state: ActivityState
    app_name: str | None = None
    app_bundle_id: str | None = None
    window_title: str | None = None
    process_id: int | None = None
    window_id: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "observed_at", require_aware_utc(self.observed_at))
        if self.process_id is not None and self.process_id <= 0:
            raise ValueError("process ID must be positive when provided")
        if self.window_id is not None and self.window_id <= 0:
            raise ValueError("window ID must be positive when provided")
        if self.window_id is not None and self.process_id is None:
            raise ValueError("window ID requires a process ID")
        if self.activity_state is not ActivityState.ACTIVE and any(
            value is not None
            for value in (
                self.app_name,
                self.app_bundle_id,
                self.window_title,
                self.process_id,
                self.window_id,
            )
        ):
            raise ValueError("inactive samples must not contain application metadata")


class ActivitySampler(Protocol):
    """Read one bounded metadata and system-state sample."""

    def sample(self) -> ActivitySample: ...


class CollectionControls(Protocol):
    """Read the live control state shared with CLI and menu-bar clients."""

    def control(self) -> CollectionControl: ...

    def rules(self, *, enabled_only: bool = False) -> tuple[ExclusionRule, ...]: ...


@dataclass(frozen=True, slots=True)
class _SegmentIdentity:
    source_type: SourceType
    activity_state: ActivityState
    app_name: str | None = None
    app_bundle_id: str | None = None
    window_title: str | None = None
    excluded: bool = False
    exclusion_reason: str | None = None


@dataclass(frozen=True, slots=True)
class _OpenSegment:
    identity: _SegmentIdentity
    started_at: datetime


class _Segmenter:
    """Turn changing point samples into immutable, bounded duration records."""

    def __init__(
        self,
        *,
        identifiers: IdentifierSource,
        retention: timedelta,
        maximum_duration: timedelta,
    ) -> None:
        if not timedelta(0) < retention <= timedelta(hours=48):
            raise ValueError("raw retention must be between zero and 48 hours")
        if maximum_duration <= timedelta(0):
            raise ValueError("maximum segment duration must be positive")
        self._identifiers = identifiers
        self._retention = retention
        self._maximum_duration = maximum_duration
        self._open: _OpenSegment | None = None
        self._last_observed_at: datetime | None = None

    def observe(
        self, identity: _SegmentIdentity | None, *, at: datetime
    ) -> tuple[Observation, ...]:
        observed_at = require_aware_utc(at)
        if self._last_observed_at is not None and observed_at < self._last_observed_at:
            raise ValueError("activity samples must be ordered by time")
        self._last_observed_at = observed_at
        completed: list[Observation] = []

        while (
            self._open is not None
            and observed_at - self._open.started_at >= self._maximum_duration
        ):
            boundary = self._open.started_at + self._maximum_duration
            completed.append(self._build_observation(self._open, ended_at=boundary))
            self._open = _OpenSegment(
                identity=self._open.identity,
                started_at=boundary,
            )

        if self._open is not None and identity != self._open.identity:
            if observed_at > self._open.started_at:
                completed.append(
                    self._build_observation(self._open, ended_at=observed_at)
                )
            self._open = None
        if identity is not None and self._open is None:
            self._open = _OpenSegment(identity=identity, started_at=observed_at)
        return tuple(completed)

    def close(self, *, at: datetime) -> tuple[Observation, ...]:
        return self.observe(None, at=at)

    def _build_observation(
        self, segment: _OpenSegment, *, ended_at: datetime
    ) -> Observation:
        identity = segment.identity
        idempotency_key = build_idempotency_key(
            "continuous-activity-segment-v1",
            identity.source_type,
            identity.activity_state,
            identity.app_name,
            identity.app_bundle_id,
            identity.window_title,
            identity.excluded,
            identity.exclusion_reason,
            segment.started_at,
            ended_at,
        )
        return Observation(
            id=self._identifiers.new(),
            idempotency_key=idempotency_key,
            source_type=identity.source_type,
            activity_state=identity.activity_state,
            captured_at=ended_at,
            started_at=segment.started_at,
            ended_at=ended_at,
            app_name=identity.app_name,
            app_bundle_id=identity.app_bundle_id,
            window_title=identity.window_title,
            excluded=identity.excluded,
            exclusion_reason=identity.exclusion_reason,
            expires_at=ended_at + self._retention,
            created_at=ended_at,
        )


class ContinuousActivityCollector:
    """Collect duration records while enforcing pause and exclusions live."""

    def __init__(
        self,
        sampler: ActivitySampler,
        *,
        controls: CollectionControls,
        policy: CollectionPolicy,
        clock: Clock,
        identifiers: IdentifierSource,
        retention: timedelta,
        maximum_segment_duration: timedelta = timedelta(seconds=60),
        retain_excluded_activity: bool = False,
    ) -> None:
        self._sampler = sampler
        self._controls = controls
        self._policy = policy
        self._clock = clock
        self._retain_excluded_activity = retain_excluded_activity
        self._system_segments = _Segmenter(
            identifiers=identifiers,
            retention=retention,
            maximum_duration=maximum_segment_duration,
        )
        self._metadata_segments = _Segmenter(
            identifiers=identifiers,
            retention=retention,
            maximum_duration=maximum_segment_duration,
        )
        self._excluded_segments = _Segmenter(
            identifiers=identifiers,
            retention=retention,
            maximum_duration=maximum_segment_duration,
        )

    def collect(self) -> tuple[Observation, ...]:
        """Collect one poll cycle; no source API is called while paused."""
        now = self._clock.now()
        control = self._controls.control()
        if control.is_paused(at=now):
            return self._interrupt(at=now)

        sample = self._sampler.sample()
        rules = self._controls.rules(enabled_only=True)
        return self.collect_sample(sample, control=control, rules=rules)

    def collect_sample(
        self,
        sample: ActivitySample,
        *,
        control: CollectionControl,
        rules: tuple[ExclusionRule, ...],
    ) -> tuple[Observation, ...]:
        """Segment one already-read sample for a shared collection cycle."""
        if control.is_paused(at=sample.observed_at):
            return self._interrupt(at=sample.observed_at)
        records = list(
            self._system_segments.observe(
                _SegmentIdentity(
                    source_type=SourceType.SYSTEM_STATE,
                    activity_state=sample.activity_state,
                ),
                at=sample.observed_at,
            )
        )
        metadata_identity: _SegmentIdentity | None = None
        excluded_identity: _SegmentIdentity | None = None
        if sample.activity_state is ActivityState.ACTIVE and (
            sample.app_name is not None or sample.app_bundle_id is not None
        ):
            decision = self._policy.evaluate(
                CollectionContext(
                    activity_state=sample.activity_state,
                    app_name=sample.app_name,
                    app_bundle_id=sample.app_bundle_id,
                    window_title=sample.window_title,
                ),
                control=control,
                rules=rules,
                at=sample.observed_at,
            )
            if decision.excluded:
                if self._retain_excluded_activity:
                    excluded_identity = _SegmentIdentity(
                        source_type=SourceType.EXCLUDED_ACTIVITY,
                        activity_state=sample.activity_state,
                        excluded=True,
                        exclusion_reason=_decision_reason(
                            decision.reason_code, decision.rule_id
                        ),
                    )
            else:
                metadata_identity = _SegmentIdentity(
                    source_type=SourceType.ACTIVE_APP,
                    activity_state=sample.activity_state,
                    app_name=sample.app_name,
                    app_bundle_id=sample.app_bundle_id,
                    window_title=sample.window_title,
                )
        records.extend(
            self._metadata_segments.observe(
                metadata_identity,
                at=sample.observed_at,
            )
        )
        records.extend(
            self._excluded_segments.observe(
                excluded_identity,
                at=sample.observed_at,
            )
        )
        return tuple(records)

    def interrupt(self, *, at: datetime) -> tuple[Observation, ...]:
        """Flush open segments without consulting any collection source."""
        return self._interrupt(at=at)

    def close(self) -> tuple[Observation, ...]:
        """Flush non-empty segments during an orderly daemon shutdown."""
        return self._interrupt(at=self._clock.now())

    def _interrupt(self, *, at: datetime) -> tuple[Observation, ...]:
        return (
            *self._system_segments.close(at=at),
            *self._metadata_segments.close(at=at),
            *self._excluded_segments.close(at=at),
        )


def _decision_reason(reason_code: str | None, rule_id: str | None) -> str:
    if reason_code == "excluded_by_rule" and rule_id is not None:
        return f"excluded_by_rule:{rule_id}"
    return reason_code or "excluded_by_policy"
