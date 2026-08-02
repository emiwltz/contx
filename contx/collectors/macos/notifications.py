"""Documented NSWorkspace notification bridge for system activity state."""

from __future__ import annotations

import sys
from collections.abc import Callable
from importlib import import_module
from types import ModuleType
from typing import Protocol, cast

from contx.collectors.macos.activity import SystemSignals
from contx.errors import CollectorUnavailableError

NotificationCallback = Callable[[object], None]


class NotificationCenter(Protocol):
    def addObserverForName_object_queue_usingBlock_(
        self,
        name: object,
        observed_object: object | None,
        queue: object | None,
        callback: NotificationCallback,
    ) -> object: ...

    def removeObserver_(self, observer: object) -> None: ...


class Workspace(Protocol):
    def notificationCenter(self) -> NotificationCenter: ...


class WorkspaceType(Protocol):
    def sharedWorkspace(self) -> Workspace: ...


class WorkspaceNotificationMonitor:
    """Translate public workspace notifications into thread-safe state signals."""

    def __init__(
        self,
        signals: SystemSignals,
        *,
        appkit: ModuleType | None = None,
    ) -> None:
        self._signals = signals
        self._appkit = appkit
        self._center: NotificationCenter | None = None
        self._tokens: list[object] = []
        self._callbacks: list[NotificationCallback] = []

    def start(self) -> None:
        if self._center is not None:
            return
        appkit = self._appkit or _load_appkit()
        try:
            workspace_type = cast(WorkspaceType, appkit.NSWorkspace)
            center = workspace_type.sharedWorkspace().notificationCenter()
            registrations = (
                (
                    appkit.NSWorkspaceSessionDidBecomeActiveNotification,
                    self._signals.session_became_active,
                ),
                (
                    appkit.NSWorkspaceSessionDidResignActiveNotification,
                    self._signals.session_resigned_active,
                ),
                (
                    appkit.NSWorkspaceWillSleepNotification,
                    self._signals.will_sleep,
                ),
                (
                    appkit.NSWorkspaceDidWakeNotification,
                    self._signals.did_wake,
                ),
            )
        except (AttributeError, TypeError) as error:
            raise CollectorUnavailableError(
                "Required macOS workspace notifications are unavailable"
            ) from error

        tokens: list[object] = []
        callbacks: list[NotificationCallback] = []
        try:
            for name, action in registrations:
                callback = _notification_callback(action)
                token = center.addObserverForName_object_queue_usingBlock_(
                    name,
                    None,
                    None,
                    callback,
                )
                callbacks.append(callback)
                tokens.append(token)
        except Exception as error:
            cleanup_failed = False
            for token in tokens:
                try:
                    center.removeObserver_(token)
                except Exception:
                    cleanup_failed = True
            if cleanup_failed:
                raise CollectorUnavailableError(
                    "Workspace notification registration failed and cleanup "
                    "was incomplete"
                ) from error
            raise CollectorUnavailableError(
                "Cannot register macOS workspace notifications"
            ) from error
        self._center = center
        self._callbacks = callbacks
        self._tokens = tokens

    def stop(self) -> None:
        if self._center is None:
            return
        center = self._center
        tokens = self._tokens
        self._center = None
        self._tokens = []
        self._callbacks = []
        failed = False
        for token in tokens:
            try:
                center.removeObserver_(token)
            except Exception:
                failed = True
        if failed:
            raise CollectorUnavailableError(
                "Cannot remove all macOS workspace notification observers"
            )

    def __enter__(self) -> WorkspaceNotificationMonitor:
        self.start()
        return self

    def __exit__(self, *_error: object) -> None:
        self.stop()


def _notification_callback(action: Callable[[], None]) -> NotificationCallback:
    def callback(_notification: object) -> None:
        action()

    return callback


def _load_appkit() -> ModuleType:
    if sys.platform != "darwin":
        raise CollectorUnavailableError(
            "Workspace notifications are available only on macOS"
        )
    try:
        return import_module("AppKit")
    except ImportError as error:
        raise CollectorUnavailableError(
            "The macOS Cocoa bridge is not installed or unavailable"
        ) from error
