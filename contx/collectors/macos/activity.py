"""Composable metadata-only macOS activity probes."""

from __future__ import annotations

import math
import sys
from dataclasses import dataclass
from importlib import import_module
from threading import Lock
from typing import Protocol, cast

from contx.collection.continuous import ActivitySample
from contx.errors import CollectorUnavailableError
from contx.models import ActivityState, Clock


class RunningApplication(Protocol):
    def localizedName(self) -> str | None: ...

    def bundleIdentifier(self) -> str | None: ...

    def processIdentifier(self) -> int: ...


class Workspace(Protocol):
    def frontmostApplication(self) -> RunningApplication | None: ...


@dataclass(frozen=True, slots=True)
class ApplicationMetadata:
    """Non-sensitive identity read before optional window or screen access."""

    app_name: str | None
    app_bundle_id: str | None
    process_id: int

    def __post_init__(self) -> None:
        if not self.app_name and not self.app_bundle_id:
            raise ValueError("application metadata requires a usable identity")
        if self.process_id <= 0:
            raise ValueError("application metadata requires a positive process ID")


class ApplicationProbe(Protocol):
    def read(self) -> ApplicationMetadata: ...


class WorkspaceApplicationProbe:
    """Read only the public identity of the frontmost AppKit application."""

    def __init__(self, workspace: Workspace | None = None) -> None:
        self._workspace = workspace

    def read(self) -> ApplicationMetadata:
        workspace = self._workspace or _load_workspace()
        try:
            application = workspace.frontmostApplication()
            if application is None:
                raise CollectorUnavailableError(
                    "macOS did not report a frontmost application"
                )
            app_name = _normalize_identity(application.localizedName())
            bundle_id = _normalize_identity(application.bundleIdentifier())
            process_id = int(application.processIdentifier())
        except CollectorUnavailableError:
            raise
        except Exception as error:
            raise CollectorUnavailableError(
                "Cannot read frontmost macOS application metadata"
            ) from error
        try:
            return ApplicationMetadata(
                app_name=app_name,
                app_bundle_id=bundle_id,
                process_id=process_id,
            )
        except ValueError as error:
            raise CollectorUnavailableError(
                "The frontmost macOS application has no usable identity metadata"
            ) from error


@dataclass(frozen=True, slots=True)
class SystemSignalSnapshot:
    session_active: bool
    asleep: bool


class SystemSignals:
    """Thread-safe state updated by documented workspace notifications."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._session_active = True
        self._asleep = False

    def session_became_active(self) -> None:
        with self._lock:
            self._session_active = True

    def session_resigned_active(self) -> None:
        with self._lock:
            self._session_active = False

    def will_sleep(self) -> None:
        with self._lock:
            self._asleep = True

    def did_wake(self) -> None:
        with self._lock:
            self._asleep = False

    def snapshot(self) -> SystemSignalSnapshot:
        with self._lock:
            return SystemSignalSnapshot(
                session_active=self._session_active,
                asleep=self._asleep,
            )


class IdleSecondsProbe(Protocol):
    def seconds_since_input(self) -> float: ...


class ActivityStateProbe(Protocol):
    def current_state(self) -> ActivityState: ...


class QuartzApi(Protocol):
    kCGEventSourceStateCombinedSessionState: int
    kCGAnyInputEventType: int

    def CGEventSourceSecondsSinceLastEventType(
        self, state_id: int, event_type: int
    ) -> float: ...


class QuartzIdleSecondsProbe:
    """Read elapsed user-input time through the public CoreGraphics API."""

    def __init__(self, quartz: QuartzApi | None = None) -> None:
        self._quartz = quartz

    def seconds_since_input(self) -> float:
        quartz = self._quartz or _load_quartz()
        try:
            return float(
                quartz.CGEventSourceSecondsSinceLastEventType(
                    quartz.kCGEventSourceStateCombinedSessionState,
                    quartz.kCGAnyInputEventType,
                )
            )
        except Exception as error:
            raise CollectorUnavailableError(
                "Cannot read elapsed macOS user-input time"
            ) from error


class ResolvedSystemStateProbe:
    """Resolve sleep and session signals before consulting user-input idle time."""

    def __init__(
        self,
        *,
        signals: SystemSignals,
        idle: IdleSecondsProbe,
        idle_threshold_seconds: float,
    ) -> None:
        if idle_threshold_seconds <= 0:
            raise ValueError("idle threshold must be positive")
        self._signals = signals
        self._idle = idle
        self._idle_threshold_seconds = idle_threshold_seconds

    def current_state(self) -> ActivityState:
        signals = self._signals.snapshot()
        if signals.asleep:
            return ActivityState.ASLEEP
        if not signals.session_active:
            return ActivityState.LOCKED
        try:
            idle_seconds = float(self._idle.seconds_since_input())
        except Exception as error:
            raise CollectorUnavailableError(
                "Cannot determine macOS idle state"
            ) from error
        if not math.isfinite(idle_seconds) or idle_seconds < 0:
            raise CollectorUnavailableError("macOS returned an invalid idle duration")
        if idle_seconds >= self._idle_threshold_seconds:
            return ActivityState.IDLE
        return ActivityState.ACTIVE


class MacOSActivitySampler:
    """Read application identity only when the user session is currently active."""

    def __init__(
        self,
        *,
        clock: Clock,
        state: ActivityStateProbe,
        application: ApplicationProbe,
    ) -> None:
        self._clock = clock
        self._state = state
        self._application = application

    def sample(self) -> ActivitySample:
        activity_state = self._state.current_state()
        if activity_state is not ActivityState.ACTIVE:
            return ActivitySample(
                observed_at=self._clock.now(),
                activity_state=activity_state,
            )
        application = self._application.read()
        return ActivitySample(
            observed_at=self._clock.now(),
            activity_state=activity_state,
            app_name=application.app_name,
            app_bundle_id=application.app_bundle_id,
            process_id=application.process_id,
        )


def _normalize_identity(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _load_workspace() -> Workspace:
    if sys.platform != "darwin":
        raise CollectorUnavailableError(
            "Active-application collection is available only on macOS"
        )
    try:
        appkit = import_module("AppKit")
        workspace = appkit.NSWorkspace.sharedWorkspace()
    except (ImportError, AttributeError) as error:
        raise CollectorUnavailableError(
            "The macOS Cocoa bridge is not installed or unavailable"
        ) from error
    return cast(Workspace, workspace)


def _load_quartz() -> QuartzApi:
    if sys.platform != "darwin":
        raise CollectorUnavailableError("Idle detection is available only on macOS")
    try:
        quartz = import_module("Quartz")
        required = (
            "CGEventSourceSecondsSinceLastEventType",
            "kCGEventSourceStateCombinedSessionState",
            "kCGAnyInputEventType",
        )
        if not all(hasattr(quartz, name) for name in required):
            raise AttributeError
    except (ImportError, AttributeError) as error:
        raise CollectorUnavailableError(
            "The macOS Quartz bridge is not installed or unavailable"
        ) from error
    return cast(QuartzApi, quartz)
