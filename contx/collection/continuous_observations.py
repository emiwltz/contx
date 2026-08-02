"""Coordinate one privacy-gated sample across continuous observation sources."""

from __future__ import annotations

from contx.collection.continuous import (
    ActivitySampler,
    CollectionControls,
    ContinuousActivityCollector,
)
from contx.collection.screenshot_capture import SelectiveScreenshotService
from contx.models import Clock, Observation


class ContinuousObservationCollector:
    """Fan one metadata sample into duration and optional screenshot collection."""

    def __init__(
        self,
        *,
        sampler: ActivitySampler,
        controls: CollectionControls,
        activity: ContinuousActivityCollector,
        screenshots: SelectiveScreenshotService | None,
        clock: Clock,
    ) -> None:
        self._sampler = sampler
        self._controls = controls
        self._activity = activity
        self._screenshots = screenshots
        self._clock = clock

    def collect(self) -> tuple[Observation, ...]:
        """Read controls before sources, then share exactly one activity sample."""
        now = self._clock.now()
        control = self._controls.control()
        if control.is_paused(at=now):
            return self._activity.interrupt(at=now)

        sample = self._sampler.sample()
        rules = self._controls.rules(enabled_only=True)
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
