"""Continuous activity segmentation and pre-capture privacy tests."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from contx.collection import (
    ActivitySample,
    CollectionPolicy,
    ContinuousActivityCollector,
)
from contx.models import (
    ActivityState,
    CollectionControl,
    ExclusionRule,
    ExclusionRuleType,
    SourceType,
)
from tests.helpers import SequenceIdentifiers

START = datetime(2026, 8, 2, 10, 0, tzinfo=UTC)


class MutableClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def now(self) -> datetime:
        return self.value


class SequenceSampler:
    def __init__(self, samples: tuple[ActivitySample, ...]) -> None:
        self._samples = iter(samples)
        self.calls = 0

    def sample(self) -> ActivitySample:
        self.calls += 1
        return next(self._samples)


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


def test_application_change_closes_duration_without_collecting_window_title() -> None:
    clock = MutableClock(START)
    sampler = SequenceSampler(
        (
            _active_sample(START, "Editor", "com.example.editor"),
            _active_sample(START + timedelta(seconds=10), "Terminal", "com.terminal"),
        )
    )
    collector = _collector(clock, sampler)

    assert collector.collect() == ()
    clock.value = START + timedelta(seconds=10)
    changed = collector.collect()
    clock.value = START + timedelta(seconds=20)
    closed = collector.close()

    assert len(changed) == 1
    assert changed[0].source_type is SourceType.ACTIVE_APP
    assert changed[0].app_name == "Editor"
    assert changed[0].started_at == START
    assert changed[0].ended_at == START + timedelta(seconds=10)
    assert changed[0].window_title is None
    assert {record.source_type for record in closed} == {
        SourceType.SYSTEM_STATE,
        SourceType.ACTIVE_APP,
    }
    terminal = next(
        record for record in closed if record.source_type is SourceType.ACTIVE_APP
    )
    assert terminal.app_name == "Terminal"
    assert terminal.started_at == START + timedelta(seconds=10)
    assert terminal.ended_at == START + timedelta(seconds=20)


def test_idle_transition_removes_application_metadata_and_tracks_state() -> None:
    clock = MutableClock(START)
    sampler = SequenceSampler(
        (
            _active_sample(START, "Editor", "com.example.editor"),
            ActivitySample(
                observed_at=START + timedelta(seconds=10),
                activity_state=ActivityState.IDLE,
            ),
        )
    )
    collector = _collector(clock, sampler)

    collector.collect()
    clock.value = START + timedelta(seconds=10)
    transition = collector.collect()
    clock.value = START + timedelta(seconds=20)
    closed = collector.close()

    assert {record.source_type for record in transition} == {
        SourceType.SYSTEM_STATE,
        SourceType.ACTIVE_APP,
    }
    idle = next(
        record for record in closed if record.source_type is SourceType.SYSTEM_STATE
    )
    assert idle.activity_state is ActivityState.IDLE
    assert idle.app_name is None
    assert idle.app_bundle_id is None
    assert idle.window_title is None


def test_excluded_application_never_enters_a_duration_record() -> None:
    rule = ExclusionRule(
        id=UUID(int=100),
        rule_type=ExclusionRuleType.APP_BUNDLE_ID,
        pattern="com.example.private",
        created_at=START,
        updated_at=START,
    )
    controls = MutableControls(rules=(rule,))
    clock = MutableClock(START)
    sampler = SequenceSampler(
        (
            _active_sample(START, "Editor", "com.example.editor"),
            _active_sample(
                START + timedelta(seconds=10),
                "Synthetic Password Manager",
                "com.example.private",
            ),
            _active_sample(
                START + timedelta(seconds=20), "Editor", "com.example.editor"
            ),
        )
    )
    collector = _collector(clock, sampler, controls=controls)

    all_records = list(collector.collect())
    clock.value = START + timedelta(seconds=10)
    all_records.extend(collector.collect())
    clock.value = START + timedelta(seconds=20)
    all_records.extend(collector.collect())
    clock.value = START + timedelta(seconds=30)
    all_records.extend(collector.close())

    serialized = " ".join(
        value
        for record in all_records
        for value in (record.app_name, record.app_bundle_id, record.window_title)
        if value is not None
    )
    assert "Password" not in serialized
    assert "com.example.private" not in serialized
    assert (
        len(
            [
                record
                for record in all_records
                if record.source_type is SourceType.ACTIVE_APP
            ]
        )
        == 2
    )


def test_optional_excluded_segment_contains_only_generic_reason() -> None:
    rule = ExclusionRule(
        id=UUID(int=100),
        rule_type=ExclusionRuleType.APP_BUNDLE_ID,
        pattern="com.example.private",
        created_at=START,
        updated_at=START,
    )
    controls = MutableControls(rules=(rule,))
    clock = MutableClock(START)
    sampler = SequenceSampler(
        (
            _active_sample(START, "Private", "com.example.private"),
            _active_sample(
                START + timedelta(seconds=10), "Editor", "com.example.editor"
            ),
        )
    )
    collector = _collector(
        clock,
        sampler,
        controls=controls,
        retain_excluded_activity=True,
    )

    collector.collect()
    clock.value = START + timedelta(seconds=10)
    (excluded,) = collector.collect()

    assert excluded.source_type is SourceType.EXCLUDED_ACTIVITY
    assert excluded.excluded
    assert excluded.exclusion_reason == f"excluded_by_rule:{rule.id}"
    assert excluded.app_name is None
    assert excluded.app_bundle_id is None
    assert excluded.window_title is None


def test_pause_flushes_open_segments_without_sampling_source() -> None:
    controls = MutableControls()
    clock = MutableClock(START)
    sampler = SequenceSampler((_active_sample(START, "Editor", "com.editor"),))
    collector = _collector(clock, sampler, controls=controls)

    collector.collect()
    controls.paused = True
    clock.value = START + timedelta(seconds=10)
    flushed = collector.collect()

    assert sampler.calls == 1
    assert {record.source_type for record in flushed} == {
        SourceType.SYSTEM_STATE,
        SourceType.ACTIVE_APP,
    }
    assert {record.ended_at for record in flushed} == {START + timedelta(seconds=10)}


def test_long_poll_gap_is_split_into_bounded_records() -> None:
    clock = MutableClock(START)
    sampler = SequenceSampler(
        (
            _active_sample(START, "Editor", "com.editor"),
            _active_sample(START + timedelta(seconds=130), "Editor", "com.editor"),
        )
    )
    collector = _collector(clock, sampler)

    collector.collect()
    clock.value = START + timedelta(seconds=130)
    records = collector.collect()
    clock.value = START + timedelta(seconds=140)
    records += collector.close()

    for source_type in (SourceType.SYSTEM_STATE, SourceType.ACTIVE_APP):
        durations = [
            record.ended_at - record.started_at
            for record in records
            if record.source_type is source_type
            and record.started_at is not None
            and record.ended_at is not None
        ]
        assert durations == [
            timedelta(seconds=60),
            timedelta(seconds=60),
            timedelta(seconds=20),
        ]


def test_non_active_sample_rejects_application_metadata() -> None:
    try:
        ActivitySample(
            observed_at=START,
            activity_state=ActivityState.LOCKED,
            app_name="Must not persist",
        )
    except ValueError as error:
        assert "must not contain" in str(error)
    else:
        raise AssertionError("non-active metadata was accepted")


def _collector(
    clock: MutableClock,
    sampler: SequenceSampler,
    *,
    controls: MutableControls | None = None,
    retain_excluded_activity: bool = False,
) -> ContinuousActivityCollector:
    return ContinuousActivityCollector(
        sampler,
        controls=controls or MutableControls(),
        policy=CollectionPolicy(),
        clock=clock,
        identifiers=SequenceIdentifiers(UUID(int=value) for value in range(1, 100)),
        retention=timedelta(hours=48),
        maximum_segment_duration=timedelta(seconds=60),
        retain_excluded_activity=retain_excluded_activity,
    )


def _active_sample(at: datetime, app_name: str, bundle_id: str) -> ActivitySample:
    return ActivitySample(
        observed_at=at,
        activity_state=ActivityState.ACTIVE,
        app_name=app_name,
        app_bundle_id=bundle_id,
    )
