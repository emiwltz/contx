"""Selective screenshot planning never reads pixels or bypasses exclusions."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from contx.collection import (
    ActivitySample,
    CollectionPolicy,
    ScreenshotTrigger,
    SelectiveScreenshotPlanner,
)
from contx.models import (
    ActivityState,
    CollectionControl,
    ExclusionRule,
    ExclusionRuleType,
)

NOW = datetime(2026, 8, 2, 16, 0, tzinfo=UTC)


def test_disabled_planner_never_selects_a_capture() -> None:
    decision = _planner(enabled=False).evaluate(
        _sample(NOW),
        previous_sample=None,
        last_capture_at=None,
        control=_control(),
        rules=(),
    )

    assert not decision.capture
    assert decision.reason_code == "screenshots_disabled"


def test_pause_and_exclusion_win_before_every_capture_trigger() -> None:
    paused = _control().pause(at=NOW)
    rule = ExclusionRule(
        id=UUID(int=1),
        rule_type=ExclusionRuleType.APP_BUNDLE_ID,
        pattern="com.example.editor",
        created_at=NOW,
        updated_at=NOW,
    )
    planner = _planner()

    paused_decision = planner.evaluate(
        _sample(NOW),
        previous_sample=None,
        last_capture_at=None,
        control=paused,
        rules=(),
        manual_requested=True,
    )
    excluded_decision = planner.evaluate(
        _sample(NOW),
        previous_sample=None,
        last_capture_at=None,
        control=_control(),
        rules=(rule,),
        manual_requested=True,
    )

    assert not paused_decision.capture
    assert paused_decision.reason_code == "collection_paused"
    assert not excluded_decision.capture
    assert excluded_decision.reason_code == "excluded_by_rule"


def test_non_active_state_never_creates_an_automatic_or_manual_capture() -> None:
    idle = ActivitySample(observed_at=NOW, activity_state=ActivityState.IDLE)

    automatic = _planner().evaluate(
        idle,
        previous_sample=None,
        last_capture_at=None,
        control=_control(),
        rules=(),
    )
    manual = _planner().evaluate(
        idle,
        previous_sample=None,
        last_capture_at=None,
        control=_control(),
        rules=(),
        manual_requested=True,
    )

    assert not automatic.capture
    assert not manual.capture
    assert automatic.reason_code == manual.reason_code == "user_not_active"


def test_automatic_triggers_follow_meaningful_transitions() -> None:
    planner = _planner()
    started = planner.evaluate(
        _sample(NOW),
        previous_sample=None,
        last_capture_at=None,
        control=_control(),
        rules=(),
    )
    switched = planner.evaluate(
        _sample(NOW + timedelta(seconds=30), app="Terminal", bundle="com.terminal"),
        previous_sample=_sample(NOW),
        last_capture_at=NOW,
        control=_control(),
        rules=(),
    )
    returned = planner.evaluate(
        _sample(NOW + timedelta(seconds=60)),
        previous_sample=ActivitySample(
            observed_at=NOW + timedelta(seconds=30),
            activity_state=ActivityState.IDLE,
        ),
        last_capture_at=NOW,
        control=_control(),
        rules=(),
    )

    assert started.trigger is ScreenshotTrigger.ACTIVITY_STARTED
    assert switched.trigger is ScreenshotTrigger.APPLICATION_CHANGED
    assert returned.trigger is ScreenshotTrigger.RETURNED_FROM_IDLE


def test_minimum_interval_blocks_automatic_change_but_not_manual_request() -> None:
    planner = _planner()
    current = _sample(NOW + timedelta(seconds=5), app="Terminal", bundle="com.term")

    automatic = planner.evaluate(
        current,
        previous_sample=_sample(NOW),
        last_capture_at=NOW,
        control=_control(),
        rules=(),
    )
    manual = planner.evaluate(
        current,
        previous_sample=_sample(NOW),
        last_capture_at=NOW,
        control=_control(),
        rules=(),
        manual_requested=True,
    )

    assert not automatic.capture
    assert automatic.reason_code == "minimum_interval_not_reached"
    assert manual.trigger is ScreenshotTrigger.MANUAL


def test_maximum_interval_requires_continuous_active_context() -> None:
    decision = _planner().evaluate(
        _sample(NOW + timedelta(seconds=120)),
        previous_sample=_sample(NOW + timedelta(seconds=110)),
        last_capture_at=NOW,
        control=_control(),
        rules=(),
    )

    assert decision.capture
    assert decision.trigger is ScreenshotTrigger.MAXIMUM_INTERVAL


def _planner(*, enabled: bool = True) -> SelectiveScreenshotPlanner:
    return SelectiveScreenshotPlanner(
        policy=CollectionPolicy(),
        enabled=enabled,
        minimum_interval=timedelta(seconds=15),
        maximum_interval=timedelta(seconds=120),
    )


def _control() -> CollectionControl:
    return CollectionControl(updated_at=NOW)


def _sample(
    at: datetime,
    *,
    app: str = "Editor",
    bundle: str = "com.example.editor",
    title: str | None = None,
) -> ActivitySample:
    return ActivitySample(
        observed_at=at,
        activity_state=ActivityState.ACTIVE,
        app_name=app,
        app_bundle_id=bundle,
        window_title=title,
    )
