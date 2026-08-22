"""Disabled-by-default daemon composition and isolated synthetic execution."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

import contx.daemon.factory as factory_module
from contx.collection import ActivitySample
from contx.daemon import build_macos_collection_daemon
from contx.db import create_database_engine, session_scope
from contx.db.models import ObservationModel
from contx.errors import ConfigurationError
from contx.models import ActivityState
from contx.settings import RuntimePaths, initialize_runtime_paths

START = datetime(2026, 8, 2, 20, 0, tzinfo=UTC)


class MutableClock:
    def __init__(self) -> None:
        self.value = START

    def now(self) -> datetime:
        return self.value


class FixedSampler:
    def sample(self) -> ActivitySample:
        return ActivitySample(
            observed_at=START,
            activity_state=ActivityState.ACTIVE,
            app_name="Synthetic Editor",
            app_bundle_id="com.example.editor",
        )


class RecordingComponent:
    def __init__(self) -> None:
        self.started = 0
        self.stopped = 0

    def start(self) -> None:
        self.started += 1

    def refresh(self) -> object:
        return object()

    def stop(self) -> None:
        self.stopped += 1


class RecordingLease:
    def __init__(self) -> None:
        self.acquired = 0
        self.released = 0

    def acquire(self) -> None:
        self.acquired += 1

    def release(self) -> None:
        self.released += 1


class FakeTimer:
    def __init__(self) -> None:
        self.invalidated = False

    def invalidate(self) -> None:
        self.invalidated = True


class FakeScheduler:
    def __init__(self) -> None:
        self.callback: Callable[[], None] | None = None
        self.timer = FakeTimer()

    def schedule(
        self,
        *,
        interval_seconds: float,
        callback: Callable[[], None],
    ) -> FakeTimer:
        assert interval_seconds == 1.0
        self.callback = callback
        return self.timer

    def fire(self) -> None:
        assert self.callback is not None
        self.callback()


class FakeApplication:
    def __init__(self) -> None:
        self.on_run: Callable[[], None] | None = None
        self.finish_calls = 0
        self.stops = 0
        self.posted_events: list[tuple[object, bool]] = []

    def finishLaunching(self) -> None:
        self.finish_calls += 1

    def run(self) -> None:
        assert self.on_run is not None
        self.on_run()

    def stop_(self, sender: object | None) -> None:
        assert sender is None
        self.stops += 1

    def postEvent_atStart_(self, event: object, at_start: bool) -> None:
        self.posted_events.append((event, at_start))


class FakeSignalApi:
    SIGINT = 2
    SIGTERM = 15

    def __init__(self) -> None:
        self.handlers: dict[int, object] = {2: "int", 15: "term"}

    def getsignal(self, signal_number: int) -> object:
        return self.handlers[signal_number]

    def signal(self, signal_number: int, handler: object) -> object:
        previous = self.handlers[signal_number]
        self.handlers[signal_number] = handler
        return previous


def test_default_configuration_refuses_to_build_background_daemon(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)

    with pytest.raises(ConfigurationError, match="disabled"):
        build_macos_collection_daemon(paths=paths)

    assert not paths.database_file.exists()


def test_enabled_factory_runs_one_isolated_synthetic_cycle(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    initialize_runtime_paths(paths)
    configured = paths.config_file.read_text().replace(
        "background_collection_enabled = false",
        "background_collection_enabled = true",
    )
    paths.config_file.write_text(configured)
    clock = MutableClock()
    scheduler = FakeScheduler()
    application = FakeApplication()
    notifications = RecordingComponent()
    menu = RecordingComponent()
    lease = RecordingLease()
    signal_api = FakeSignalApi()
    stop_event = object()
    daemon = build_macos_collection_daemon(
        paths=paths,
        clock=clock,
        sampler=FixedSampler(),
        notifications=notifications,
        menu=menu,
        lease=lease,
        application=application,
        scheduler=scheduler,
        stop_event_factory=lambda: stop_event,
        signal_api=signal_api,
    )

    def run_cycle() -> None:
        scheduler.fire()
        clock.value = START + timedelta(seconds=10)
        daemon.request_stop()
        scheduler.fire()

    application.on_run = run_cycle
    result = daemon.run()

    assert result is not None
    assert result.cycles == 1
    assert result.observations == 2
    assert scheduler.timer.invalidated
    assert application.finish_calls == 1
    assert application.stops == 1
    assert application.posted_events == [(stop_event, True)]
    assert notifications.started == notifications.stopped == 1
    assert menu.started == menu.stopped == 1
    assert lease.acquired == lease.released == 1
    assert signal_api.handlers == {2: "int", 15: "term"}
    engine = create_database_engine(paths.database_file)
    try:
        with session_scope(engine) as session:
            persisted = tuple(session.scalars(select(ObservationModel)))
        assert len(persisted) == 2
        assert {record.source_type for record in persisted} == {
            "system_state",
            "active_app",
        }
    finally:
        engine.dispose()


def test_enabled_screenshot_factory_uses_the_native_screencapturekit_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    initialize_runtime_paths(paths)
    configured = (
        paths.config_file.read_text()
        .replace(
            "background_collection_enabled = false",
            "background_collection_enabled = true",
        )
        .replace("screenshots_enabled = false", "screenshots_enabled = true")
    )
    paths.config_file.write_text(configured)
    native_sources: list[object] = []

    class NativeSource:
        def capture_png(self, sample: ActivitySample) -> bytes:
            return b"synthetic native png"

    def build_native_source(**_dependencies: object) -> NativeSource:
        source = NativeSource()
        native_sources.append(source)
        return source

    monkeypatch.setattr(
        factory_module,
        "ScreenCaptureKitScreenshotSource",
        build_native_source,
    )
    daemon = build_macos_collection_daemon(
        paths=paths,
        clock=MutableClock(),
        sampler=FixedSampler(),
        notifications=RecordingComponent(),
        menu=RecordingComponent(),
        lease=RecordingLease(),
        application=FakeApplication(),
        scheduler=FakeScheduler(),
        signal_api=FakeSignalApi(),
    )

    assert len(native_sources) == 1
    daemon.close()


def _paths(root: Path) -> RuntimePaths:
    return RuntimePaths(
        application_support=root / "application-support",
        caches=root / "caches",
        logs=root / "logs",
    )
