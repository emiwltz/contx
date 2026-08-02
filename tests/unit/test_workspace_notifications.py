"""Documented workspace notifications update system state without content."""

from collections.abc import Callable
from types import ModuleType

import pytest

from contx.collectors.macos import SystemSignals, WorkspaceNotificationMonitor
from contx.errors import CollectorUnavailableError

SESSION_ACTIVE = "session-active"
SESSION_INACTIVE = "session-inactive"
WILL_SLEEP = "will-sleep"
DID_WAKE = "did-wake"


class FakeNotificationCenter:
    def __init__(self, *, fail_after: int | None = None) -> None:
        self._callbacks: dict[object, tuple[object, Callable[[object], None]]] = {}
        self._next_token = 0
        self._fail_after = fail_after
        self.removed: list[object] = []

    def addObserverForName_object_queue_usingBlock_(
        self,
        name: object,
        observed_object: object | None,
        queue: object | None,
        callback: Callable[[object], None],
    ) -> object:
        assert observed_object is None
        assert queue is None
        if self._fail_after is not None and self._next_token >= self._fail_after:
            raise RuntimeError("synthetic registration failure")
        token = self._next_token
        self._next_token += 1
        self._callbacks[token] = (name, callback)
        return token

    def removeObserver_(self, observer: object) -> None:
        self.removed.append(observer)
        self._callbacks.pop(observer, None)

    def emit(self, name: object) -> None:
        for registered_name, callback in tuple(self._callbacks.values()):
            if registered_name == name:
                callback(object())


class FakeWorkspace:
    def __init__(self, center: FakeNotificationCenter) -> None:
        self._center = center

    def notificationCenter(self) -> FakeNotificationCenter:
        return self._center


class FakeWorkspaceType:
    def __init__(self, workspace: FakeWorkspace) -> None:
        self._workspace = workspace

    def sharedWorkspace(self) -> FakeWorkspace:
        return self._workspace


def test_notifications_update_state_and_stop_removes_every_observer() -> None:
    center = FakeNotificationCenter()
    signals = SystemSignals()
    monitor = WorkspaceNotificationMonitor(signals, appkit=_appkit(center))

    monitor.start()
    monitor.start()
    center.emit(SESSION_INACTIVE)
    assert not signals.snapshot().session_active
    center.emit(WILL_SLEEP)
    assert signals.snapshot().asleep
    center.emit(DID_WAKE)
    assert not signals.snapshot().asleep
    center.emit(SESSION_ACTIVE)
    assert signals.snapshot().session_active

    monitor.stop()
    monitor.stop()
    assert center.removed == [0, 1, 2, 3]


def test_partial_registration_failure_removes_previous_observers() -> None:
    center = FakeNotificationCenter(fail_after=2)
    monitor = WorkspaceNotificationMonitor(SystemSignals(), appkit=_appkit(center))

    with pytest.raises(CollectorUnavailableError, match="Cannot register"):
        monitor.start()

    assert center.removed == [0, 1]


def _appkit(center: FakeNotificationCenter) -> ModuleType:
    module = ModuleType("FakeAppKit")
    module.NSWorkspace = FakeWorkspaceType(FakeWorkspace(center))  # type: ignore[attr-defined]
    module.NSWorkspaceSessionDidBecomeActiveNotification = SESSION_ACTIVE  # type: ignore[attr-defined]
    module.NSWorkspaceSessionDidResignActiveNotification = SESSION_INACTIVE  # type: ignore[attr-defined]
    module.NSWorkspaceWillSleepNotification = WILL_SLEEP  # type: ignore[attr-defined]
    module.NSWorkspaceDidWakeNotification = DID_WAKE  # type: ignore[attr-defined]
    return module
