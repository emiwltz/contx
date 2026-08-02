"""Coordinate one privacy-gated sample across continuous observation sources."""

from __future__ import annotations

from dataclasses import replace
from typing import Protocol

from contx.collection.continuous import (
    ActivitySample,
    ActivitySampler,
    CollectionControls,
    ContinuousActivityCollector,
)
from contx.collection.policy import CollectionContext, CollectionPolicy
from contx.collection.screenshot_capture import SelectiveScreenshotService
from contx.errors import CollectorUnavailableError
from contx.models import (
    ActivityState,
    Clock,
    CollectionControl,
    ExclusionRule,
    Observation,
)


class WindowTitleProbe(Protocol):
    def read(self) -> str | None: ...


class ContinuousObservationCollector:
    """Fan one metadata sample into duration and optional screenshot collection."""

    def __init__(
        self,
        *,
        sampler: ActivitySampler,
        controls: CollectionControls,
        activity: ContinuousActivityCollector,
        screenshots: SelectiveScreenshotService | None,
        window_titles: WindowTitleProbe | None,
        policy: CollectionPolicy,
        clock: Clock,
    ) -> None:
        self._sampler = sampler
        self._controls = controls
        self._activity = activity
        self._screenshots = screenshots
        self._window_titles = window_titles
        self._policy = policy
        self._clock = clock

    def collect(self) -> tuple[Observation, ...]:
        """Read controls before sources, then share exactly one activity sample."""
        now = self._clock.now()
        control = self._controls.control()
        if control.is_paused(at=now):
            return self._activity.interrupt(at=now)

        rules = self._controls.rules(enabled_only=True)
        sample = self._with_optional_window_title(
            self._sampler.sample(),
            control=control,
            rules=rules,
        )
        screenshot = (
            None
            if self._screenshots is None
            else self._screenshots.consider(
                sample,
                control=control,
                rules=rules,
            ).observation
        )
        activity = self._activity.collect_sample(
            sample,
            control=control,
            rules=rules,
        )
        if screenshot is None:
            return activity
        return (*activity, screenshot)

    def close(self) -> tuple[Observation, ...]:
        """Flush only duration records; screenshots have no open state."""
        return self._activity.close()

    def _with_optional_window_title(
        self,
        sample: ActivitySample,
        *,
        control: CollectionControl,
        rules: tuple[ExclusionRule, ...],
    ) -> ActivitySample:
        if (
            self._window_titles is None
            or sample.activity_state is not ActivityState.ACTIVE
        ):
            return sample
        decision = self._policy.evaluate(
            CollectionContext(
                activity_state=sample.activity_state,
                app_name=sample.app_name,
                app_bundle_id=sample.app_bundle_id,
            ),
            control=control,
            rules=rules,
            at=sample.observed_at,
        )
        if decision.excluded:
            return sample
        try:
            title = self._window_titles.read()
        except CollectorUnavailableError:
            return sample
        return replace(sample, window_title=title)
