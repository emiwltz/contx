"""Shared-sample continuous observation coordinator tests."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from contx.collection import (
    ActivitySample,
    CollectionPolicy,
    ContinuousActivityCollector,
    ContinuousObservationCollector,
    SelectiveScreenshotPlanner,
    SelectiveScreenshotService,
)
from contx.models import (
    ActivityState,
    CollectionControl,
    ExclusionRule,
    ExclusionRuleType,
    SourceType,
)
from contx.raw_store import FilesystemRawStore
from tests.helpers import SequenceIdentifiers

START = datetime(2026, 8, 2, 17, 0, tzinfo=UTC)
SYNTHETIC_PNG = b"\x89PNG\r\n\x1a\nshared-sample-fixture"


class MutableClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def now(self) -> datetime:
        return self.value


class RecordingSampler:
    def __init__(self, sample: ActivitySample) -> None:
        self._sample = sample
        self.calls = 0

    def sample(self) -> ActivitySample:
        self.calls += 1
        return self._sample


class RecordingScreenshotSource:
    def __init__(self) -> None:
        self.calls = 0

    def capture_png(self) -> bytes:
        self.calls += 1
        return SYNTHETIC_PNG


class MutableControls:
    def __init__(self, *, rules: tuple[ExclusionRule, ...] = ()) -> None:
        self.paused = False
        self._rules = rules

    def control(self) -> CollectionControl:
        return CollectionControl(
            paused_at=START if self.paused else None,
            updated_at=START,
        )

    def rules(self, *, enabled_only: bool = False) -> tuple[ExclusionRule, ...]:
        if enabled_only:
            return tuple(rule for rule in self._rules if rule.enabled)
        return self._rules


def test_one_sample_drives_activity_and_synthetic_screenshot(tmp_path: Path) -> None:
    clock = MutableClock(START)
    sample = _sample()
    sampler = RecordingSampler(sample)
    source = RecordingScreenshotSource()
    collector = _collector(
        tmp_path,
        clock=clock,
        sampler=sampler,
        controls=MutableControls(),
        screenshot_source=source,
    )

    first = collector.collect()
    clock.value = START + timedelta(seconds=10)
    closed = collector.close()

    assert sampler.calls == 1
    assert source.calls == 1
    assert len(first) == 1
    assert first[0].source_type is SourceType.SCREENSHOT
    assert {record.source_type for record in closed} == {
        SourceType.SYSTEM_STATE,
        SourceType.ACTIVE_APP,
    }


def test_pause_flushes_segments_without_reading_any_source(tmp_path: Path) -> None:
    clock = MutableClock(START)
    sampler = RecordingSampler(_sample())
    source = RecordingScreenshotSource()
    controls = MutableControls()
    collector = _collector(
        tmp_path,
        clock=clock,
        sampler=sampler,
        controls=controls,
        screenshot_source=source,
    )
    collector.collect()

    controls.paused = True
    clock.value = START + timedelta(seconds=10)
    flushed = collector.collect()

    assert sampler.calls == 1
    assert source.calls == 1
    assert {record.source_type for record in flushed} == {
        SourceType.SYSTEM_STATE,
        SourceType.ACTIVE_APP,
    }


def test_exclusion_is_shared_before_pixel_capture_and_duration_storage(
    tmp_path: Path,
) -> None:
    rule = ExclusionRule(
        id=UUID(int=80),
        rule_type=ExclusionRuleType.APP_BUNDLE_ID,
        pattern="com.example.private",
        created_at=START,
        updated_at=START,
    )
    clock = MutableClock(START)
    sampler = RecordingSampler(_sample(bundle="com.example.private"))
    source = RecordingScreenshotSource()
    collector = _collector(
        tmp_path,
        clock=clock,
        sampler=sampler,
        controls=MutableControls(rules=(rule,)),
        screenshot_source=source,
    )

    assert collector.collect() == ()
    clock.value = START + timedelta(seconds=10)
    closed = collector.close()

    assert sampler.calls == 1
    assert source.calls == 0
    assert len(closed) == 1
    assert closed[0].source_type is SourceType.SYSTEM_STATE


def _collector(
    tmp_path: Path,
    *,
    clock: MutableClock,
    sampler: RecordingSampler,
    controls: MutableControls,
    screenshot_source: RecordingScreenshotSource,
) -> ContinuousObservationCollector:
    activity = ContinuousActivityCollector(
        sampler,
        controls=controls,
        policy=CollectionPolicy(),
        clock=clock,
        identifiers=SequenceIdentifiers(UUID(int=value) for value in range(1, 20)),
        retention=timedelta(hours=48),
    )
    screenshots = SelectiveScreenshotService(
        planner=SelectiveScreenshotPlanner(
            policy=CollectionPolicy(),
            enabled=True,
            minimum_interval=timedelta(seconds=15),
            maximum_interval=timedelta(seconds=120),
        ),
        source=screenshot_source,
        raw_store=FilesystemRawStore(tmp_path / "raw", disk_budget_bytes=1024),
        retention=timedelta(hours=48),
    )
    return ContinuousObservationCollector(
        sampler=sampler,
        controls=controls,
        activity=activity,
        screenshots=screenshots,
        clock=clock,
    )


def _sample(*, bundle: str = "com.example.editor") -> ActivitySample:
    return ActivitySample(
        observed_at=START,
        activity_state=ActivityState.ACTIVE,
        app_name="Synthetic Editor",
        app_bundle_id=bundle,
    )
