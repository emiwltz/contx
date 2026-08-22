"""AppKit timer loop for the single-process macOS collection daemon."""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from importlib import import_module
from threading import Event
from types import ModuleType
from typing import Any, Protocol, cast

from contx.application import ContinuousCollectionResult
from contx.errors import CollectorUnavailableError


class DaemonLifecycle(Protocol):
    @property
    def is_started(self) -> bool: ...

    def start(self) -> None: ...

    def tick(self) -> int: ...

    def stop(self) -> ContinuousCollectionResult | None: ...


class ApplicationLoop(Protocol):
    def finishLaunching(self) -> None: ...

    def run(self) -> None: ...

    def stop_(self, sender: object | None) -> None: ...

    def postEvent_atStart_(self, event: object, at_start: bool) -> None: ...


class ScheduledCallback(Protocol):
    def invalidate(self) -> None: ...


class CallbackScheduler(Protocol):
    def schedule(
        self,
        *,
        interval_seconds: float,
        callback: Callable[[], None],
    ) -> ScheduledCallback: ...


@dataclass(slots=True)
class _RetainedTimer:
    timer: Any
    target: Any

    def invalidate(self) -> None:
        self.timer.invalidate()


class AppKitTimerScheduler:
    """Schedule a retained Objective-C target on the current AppKit run loop."""

    def __init__(self, *, foundation: ModuleType | None = None) -> None:
        self._foundation = foundation

    def schedule(
        self,
        *,
        interval_seconds: float,
        callback: Callable[[], None],
    ) -> ScheduledCallback:
        foundation = self._foundation or _load_foundation()
        target = _build_timer_target(callback)
        try:
            schedule_timer = foundation.NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_  # noqa: E501
            timer = schedule_timer(
                interval_seconds,
                target,
                "timerFired:",
                None,
                True,
            )
        except Exception as error:
            raise CollectorUnavailableError(
                "Cannot schedule the CONTX AppKit collection timer"
            ) from error
        return _RetainedTimer(timer=timer, target=target)


class AppKitDaemonRunner:
    """Run bounded collection ticks on AppKit and stop outside native callbacks."""

    def __init__(
        self,
        *,
        lifecycle: DaemonLifecycle,
        poll_interval: timedelta,
        application: ApplicationLoop | None = None,
        scheduler: CallbackScheduler | None = None,
        stop_event_factory: Callable[[], object] | None = None,
    ) -> None:
        if poll_interval <= timedelta(0):
            raise ValueError("collection poll interval must be positive")
        self._lifecycle = lifecycle
        self._poll_interval = poll_interval
        self._application = application
        self._scheduler = scheduler
        self._stop_event_factory = stop_event_factory
        self._stop_event: object | None = None
        self._stop_requested = Event()
        self._failure: Exception | None = None
        self._running = False
        self._has_run = False

    def request_stop(self) -> None:
        """Request a main-loop stop without calling AppKit from another thread."""
        self._stop_requested.set()

    def run(self) -> ContinuousCollectionResult | None:
        if self._running or self._has_run:
            raise RuntimeError("AppKit daemon runner is single-use")
        self._has_run = True
        application = self._application or _load_application()
        self._application = application
        scheduler = self._scheduler or AppKitTimerScheduler()
        timer: ScheduledCallback | None = None
        result: ContinuousCollectionResult | None = None
        primary_error: Exception | None = None
        application.finishLaunching()
        stop_event_factory = self._stop_event_factory or _build_stop_event
        self._stop_event = stop_event_factory()
        self._lifecycle.start()
        self._running = True
        try:
            timer = scheduler.schedule(
                interval_seconds=self._poll_interval.total_seconds(),
                callback=self._timer_fired,
            )
            application.run()
            primary_error = self._failure
        except Exception as error:
            primary_error = error
        finally:
            self._running = False
            if timer is not None:
                try:
                    timer.invalidate()
                except Exception as error:
                    if primary_error is None:
                        primary_error = error
                    else:
                        primary_error.add_note(
                            "CONTX AppKit timer cleanup was incomplete"
                        )
            if self._lifecycle.is_started:
                try:
                    result = self._lifecycle.stop()
                except Exception as error:
                    if primary_error is None:
                        primary_error = error
                    else:
                        primary_error.add_note(
                            "CONTX daemon lifecycle cleanup was incomplete"
                        )
        if primary_error is not None:
            raise primary_error
        return result

    def _timer_fired(self) -> None:
        application = self._application or _load_application()
        if self._stop_requested.is_set():
            self._stop_application(application)
            return
        try:
            self._lifecycle.tick()
        except Exception as error:
            self._failure = error
            self._stop_application(application)

    def _stop_application(self, application: ApplicationLoop) -> None:
        if self._stop_event is None:
            raise RuntimeError("AppKit stop event is unavailable")
        application.stop_(None)
        application.postEvent_atStart_(self._stop_event, True)


def _build_timer_target(callback: Callable[[], None]) -> Any:
    try:
        foundation = import_module("Foundation")
        objc = import_module("objc")
        ns_object = foundation.NSObject
    except (ImportError, AttributeError) as error:
        raise CollectorUnavailableError(
            "The macOS Objective-C bridge is unavailable"
        ) from error

    class TimerTarget(ns_object):  # type: ignore[misc, valid-type]
        def initWithCallback_(self, action: Callable[[], None]) -> Any:
            initialized = objc.super(TimerTarget, self).init()
            if initialized is None:
                return None
            initialized._contx_callback = action
            return initialized

        def timerFired_(self, _timer: object) -> None:
            self._contx_callback()

    try:
        return cast(Any, TimerTarget).alloc().initWithCallback_(callback)
    except Exception as error:
        raise CollectorUnavailableError(
            "Cannot create the CONTX AppKit timer target"
        ) from error


def _load_application() -> ApplicationLoop:
    appkit = _load_appkit()
    try:
        application = appkit.NSApplication.sharedApplication()
    except AttributeError as error:
        raise CollectorUnavailableError(
            "The macOS Cocoa application API is unavailable"
        ) from error
    return cast(ApplicationLoop, application)


def _build_stop_event() -> object:
    appkit = _load_appkit()
    try:
        event = appkit.NSEvent.otherEventWithType_location_modifierFlags_timestamp_windowNumber_context_subtype_data1_data2_(  # noqa: E501
            appkit.NSEventTypeApplicationDefined,
            (0.0, 0.0),
            0,
            0.0,
            0,
            None,
            0,
            0,
            0,
        )
    except (AttributeError, TypeError) as error:
        raise CollectorUnavailableError(
            "Cannot create the CONTX AppKit stop event"
        ) from error
    if event is None:
        raise CollectorUnavailableError("macOS did not create an AppKit stop event")
    return cast(object, event)


def _load_appkit() -> ModuleType:
    if sys.platform != "darwin":
        raise CollectorUnavailableError(
            "The AppKit daemon loop is available only on macOS"
        )
    try:
        return import_module("AppKit")
    except ImportError as error:
        raise CollectorUnavailableError(
            "The macOS Cocoa bridge is not installed or unavailable"
        ) from error


def _load_foundation() -> ModuleType:
    if sys.platform != "darwin":
        raise CollectorUnavailableError("The AppKit timer is available only on macOS")
    try:
        return import_module("Foundation")
    except ImportError as error:
        raise CollectorUnavailableError(
            "The macOS Foundation bridge is not installed or unavailable"
        ) from error
