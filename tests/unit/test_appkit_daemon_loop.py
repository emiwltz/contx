"""AppKit daemon loop behavior without starting a native application."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from contx.application import ContinuousCollectionResult
from contx.daemon import AppKitDaemonRunner
from contx.errors import PipelineError
from contx.models import ProcessingRun

NOW = datetime(2026, 8, 2, 19, 0, tzinfo=UTC)


class FakeLifecycle:
    def __init__(self, *, fail_tick: bool = False) -> None:
        self.started = False
        self.fail_tick = fail_tick
        self.events: list[str] = []

    @property
    def is_started(self) -> bool:
        return self.started

    def start(self) -> None:
        self.events.append("start")
        self.started = True

    def tick(self) -> int:
        self.events.append("tick")
        if self.fail_tick:
            raise PipelineError("synthetic tick failure")
        return 0

    def stop(self) -> ContinuousCollectionResult:
        self.events.append("stop")
        self.started = False
        run = ProcessingRun(
            id=UUID(int=1),
            pipeline="test",
            version="test-v1",
            started_at=NOW,
        ).succeed(ended_at=NOW, input_count=1, output_count=0)
        return ContinuousCollectionResult(run=run, cycles=1, observations=0)


class FakeTimer:
    def __init__(self) -> None:
        self.invalidated = False

    def invalidate(self) -> None:
        self.invalidated = True


class FakeScheduler:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.interval: float | None = None
        self.callback: object | None = None
        self.timer = FakeTimer()

    def schedule(self, *, interval_seconds: float, callback: object) -> FakeTimer:
        if self.fail:
            raise PipelineError("synthetic scheduler failure")
        self.interval = interval_seconds
        self.callback = callback
        return self.timer

    def fire(self) -> None:
        assert callable(self.callback)
        self.callback()


class FakeApplication:
    def __init__(self) -> None:
        self.on_run: object | None = None
        self.finish_calls = 0
        self.stop_calls = 0
        self.posted_events: list[tuple[object, bool]] = []

    def finishLaunching(self) -> None:
        self.finish_calls += 1

    def run(self) -> None:
        assert callable(self.on_run)
        self.on_run()

    def stop_(self, sender: object | None) -> None:
        assert sender is None
        self.stop_calls += 1

    def postEvent_atStart_(self, event: object, at_start: bool) -> None:
        self.posted_events.append((event, at_start))


def test_loop_ticks_stops_on_request_and_cleans_up() -> None:
    lifecycle = FakeLifecycle()
    scheduler = FakeScheduler()
    application = FakeApplication()
    stop_event = object()
    runner = AppKitDaemonRunner(
        lifecycle=lifecycle,
        poll_interval=timedelta(seconds=2),
        application=application,
        scheduler=scheduler,
        stop_event_factory=lambda: stop_event,
    )

    def run_callbacks() -> None:
        scheduler.fire()
        runner.request_stop()
        scheduler.fire()

    application.on_run = run_callbacks

    result = runner.run()

    assert result is not None
    assert application.finish_calls == 1
    assert scheduler.interval == 2.0
    assert scheduler.timer.invalidated
    assert application.stop_calls == 1
    assert application.posted_events == [(stop_event, True)]
    assert lifecycle.events == ["start", "tick", "stop"]


def test_tick_failure_stops_appkit_and_is_raised_after_cleanup() -> None:
    lifecycle = FakeLifecycle(fail_tick=True)
    scheduler = FakeScheduler()
    application = FakeApplication()
    stop_event = object()
    runner = AppKitDaemonRunner(
        lifecycle=lifecycle,
        poll_interval=timedelta(seconds=1),
        application=application,
        scheduler=scheduler,
        stop_event_factory=lambda: stop_event,
    )
    application.on_run = scheduler.fire

    with pytest.raises(PipelineError, match="synthetic tick failure"):
        runner.run()

    assert application.finish_calls == 1
    assert application.stop_calls == 1
    assert application.posted_events == [(stop_event, True)]
    assert scheduler.timer.invalidated
    assert lifecycle.events == ["start", "tick", "stop"]


def test_timer_setup_failure_still_stops_started_lifecycle() -> None:
    lifecycle = FakeLifecycle()
    scheduler = FakeScheduler(fail=True)
    runner = AppKitDaemonRunner(
        lifecycle=lifecycle,
        poll_interval=timedelta(seconds=1),
        application=FakeApplication(),
        scheduler=scheduler,
        stop_event_factory=object,
    )

    with pytest.raises(PipelineError, match="scheduler failure"):
        runner.run()

    assert lifecycle.events == ["start", "stop"]
