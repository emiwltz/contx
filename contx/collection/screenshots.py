"""Privacy-first selective screenshot trigger planning."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from contx.collection.continuous import ActivitySample
from contx.collection.policy import CollectionContext, CollectionPolicy
from contx.models import ActivityState, CollectionControl, ExclusionRule
from contx.models.common import require_aware_utc


class ScreenshotTrigger(StrEnum):
    ACTIVITY_STARTED = "activity_started"
    APPLICATION_CHANGED = "application_changed"
    WINDOW_CHANGED = "window_changed"
    RETURNED_FROM_IDLE = "returned_from_idle"
    MAXIMUM_INTERVAL = "maximum_interval"
    MANUAL = "manual"


@dataclass(frozen=True, slots=True)
class ScreenshotDecision:
    capture: bool
    trigger: ScreenshotTrigger | None = None
    reason_code: str | None = None


class SelectiveScreenshotPlanner:
    """Plan screenshots from meaningful transitions, never a blind cadence."""

    def __init__(
        self,
        *,
        policy: CollectionPolicy,
        enabled: bool,
        minimum_interval: timedelta,
        maximum_interval: timedelta,
    ) -> None:
        if minimum_interval <= timedelta(0):
            raise ValueError("screenshot minimum interval must be positive")
        if maximum_interval < minimum_interval:
            raise ValueError("screenshot maximum interval must not be below minimum")
        self._policy = policy
        self._enabled = enabled
        self._minimum_interval = minimum_interval
        self._maximum_interval = maximum_interval

    def evaluate(
        self,
        sample: ActivitySample,
        *,
        previous_sample: ActivitySample | None,
        last_capture_at: datetime | None,
        control: CollectionControl,
        rules: tuple[ExclusionRule, ...],
        manual_requested: bool = False,
    ) -> ScreenshotDecision:
        at = require_aware_utc(sample.observed_at)
        if previous_sample is not None and previous_sample.observed_at > at:
            raise ValueError("previous screenshot sample must not be in the future")
        if last_capture_at is not None:
            captured_at = require_aware_utc(last_capture_at)
            if captured_at > at:
                raise ValueError("last screenshot time must not be in the future")
        else:
            captured_at = None

        if not self._enabled:
            return ScreenshotDecision(capture=False, reason_code="screenshots_disabled")
        exclusion = self._policy.evaluate(
            CollectionContext(
                activity_state=sample.activity_state,
                app_name=sample.app_name,
                app_bundle_id=sample.app_bundle_id,
                window_title=sample.window_title,
            ),
            control=control,
            rules=rules,
            at=at,
        )
        if exclusion.excluded:
            return ScreenshotDecision(
                capture=False,
                reason_code=exclusion.reason_code or "excluded_by_policy",
            )
        if sample.activity_state is not ActivityState.ACTIVE:
            return ScreenshotDecision(capture=False, reason_code="user_not_active")
        if manual_requested:
            return ScreenshotDecision(capture=True, trigger=ScreenshotTrigger.MANUAL)
        if captured_at is not None and at - captured_at < self._minimum_interval:
            return ScreenshotDecision(
                capture=False,
                reason_code="minimum_interval_not_reached",
            )

        trigger = _transition_trigger(sample, previous_sample)
        if trigger is not None:
            return ScreenshotDecision(capture=True, trigger=trigger)
        if captured_at is not None and at - captured_at >= self._maximum_interval:
            return ScreenshotDecision(
                capture=True,
                trigger=ScreenshotTrigger.MAXIMUM_INTERVAL,
            )
        return ScreenshotDecision(capture=False, reason_code="no_meaningful_change")


def _transition_trigger(
    current: ActivitySample,
    previous: ActivitySample | None,
) -> ScreenshotTrigger | None:
    if previous is None:
        return ScreenshotTrigger.ACTIVITY_STARTED
    if previous.activity_state is not ActivityState.ACTIVE:
        return ScreenshotTrigger.RETURNED_FROM_IDLE
    if (current.app_name, current.app_bundle_id) != (
        previous.app_name,
        previous.app_bundle_id,
    ):
        return ScreenshotTrigger.APPLICATION_CHANGED
    if (
        current.window_title is not None
        and previous.window_title is not None
        and current.window_title != previous.window_title
    ):
        return ScreenshotTrigger.WINDOW_CHANGED
    return None
