"""Minimal AppKit menu-bar control backed by durable collection services."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import timedelta
from importlib import import_module
from types import ModuleType
from typing import Any, Protocol, cast

from contx.errors import CollectorUnavailableError
from contx.models import Clock, CollectionControl


class MenuCollectionControls(Protocol):
    def control(self) -> CollectionControl: ...

    def pause(self, *, duration: timedelta | None = None) -> CollectionControl: ...

    def resume(self) -> CollectionControl: ...


@dataclass(frozen=True, slots=True)
class MenuBarSnapshot:
    button_title: str
    status_text: str
    pause_enabled: bool
    resume_enabled: bool


class MenuBarModel:
    """UI-independent menu state and actions shared with AppKit tests."""

    def __init__(
        self,
        *,
        controls: MenuCollectionControls,
        clock: Clock,
        collection_enabled: bool,
    ) -> None:
        self._controls = controls
        self._clock = clock
        self._collection_enabled = collection_enabled

    def snapshot(self) -> MenuBarSnapshot:
        if not self._collection_enabled:
            return MenuBarSnapshot(
                button_title="CONTX ○",
                status_text="Collection disabled",
                pause_enabled=False,
                resume_enabled=False,
            )
        control = self._controls.control()
        paused = control.is_paused(at=self._clock.now())
        return MenuBarSnapshot(
            button_title="CONTX Ⅱ" if paused else "CONTX ●",
            status_text="Collection paused" if paused else "Collection active",
            pause_enabled=not paused,
            resume_enabled=paused,
        )

    def pause_for_fifteen_minutes(self) -> None:
        if self._collection_enabled:
            self._controls.pause(duration=timedelta(minutes=15))

    def pause_indefinitely(self) -> None:
        if self._collection_enabled:
            self._controls.pause()

    def resume(self) -> None:
        if self._collection_enabled:
            self._controls.resume()


class NativeMenuBarController:
    """Own one removable NSStatusItem; it does not start collection itself."""

    def __init__(
        self,
        model: MenuBarModel,
        *,
        appkit: ModuleType | None = None,
    ) -> None:
        self._model = model
        self._appkit = appkit
        self._status_item: Any | None = None
        self._status_line: Any | None = None
        self._pause_fifteen: Any | None = None
        self._pause_indefinitely: Any | None = None
        self._resume: Any | None = None
        self._target: Any | None = None

    def start(self) -> None:
        if self._status_item is not None:
            return
        appkit = self._appkit or _load_appkit()
        status_bar: Any | None = None
        status_item: Any | None = None
        try:
            application = appkit.NSApplication.sharedApplication()
            application.setActivationPolicy_(
                appkit.NSApplicationActivationPolicyAccessory
            )
            status_bar = appkit.NSStatusBar.systemStatusBar()
            status_item = status_bar.statusItemWithLength_(
                appkit.NSVariableStatusItemLength
            )
            if status_item.button() is None:
                raise CollectorUnavailableError(
                    "macOS did not provide a menu-bar status button"
                )
            target = _build_action_target(self)
            menu = appkit.NSMenu.alloc().init()
            status_line = _menu_item(appkit, "Collection status", None, target)
            status_line.setEnabled_(False)
            menu.addItem_(status_line)
            menu.addItem_(appkit.NSMenuItem.separatorItem())
            pause_fifteen = _menu_item(
                appkit,
                "Pause for 15 minutes",
                "pauseFifteenMinutes:",
                target,
            )
            pause_indefinitely = _menu_item(
                appkit,
                "Pause indefinitely",
                "pauseIndefinitely:",
                target,
            )
            resume = _menu_item(
                appkit,
                "Resume collection",
                "resumeCollection:",
                target,
            )
            menu.addItem_(pause_fifteen)
            menu.addItem_(pause_indefinitely)
            menu.addItem_(resume)
            status_item.setMenu_(menu)
        except Exception as error:
            cleanup_failed = _remove_partial_status_item(status_bar, status_item)
            if isinstance(error, CollectorUnavailableError):
                if cleanup_failed:
                    error.add_note("Partial macOS menu-bar cleanup failed")
                raise
            wrapped = CollectorUnavailableError(
                "Cannot create the CONTX macOS menu-bar control"
            )
            if cleanup_failed:
                wrapped.add_note("Partial macOS menu-bar cleanup failed")
            raise wrapped from error
        self._appkit = appkit
        self._status_item = status_item
        self._status_line = status_line
        self._pause_fifteen = pause_fifteen
        self._pause_indefinitely = pause_indefinitely
        self._resume = resume
        self._target = target
        self.refresh()

    def refresh(self) -> MenuBarSnapshot:
        if self._status_item is None or self._status_line is None:
            raise RuntimeError("menu-bar controller is not started")
        assert self._pause_fifteen is not None
        assert self._pause_indefinitely is not None
        assert self._resume is not None
        snapshot = self._model.snapshot()
        self._status_item.button().setTitle_(snapshot.button_title)
        self._status_item.button().setToolTip_(snapshot.status_text)
        self._status_line.setTitle_(snapshot.status_text)
        self._pause_fifteen.setEnabled_(snapshot.pause_enabled)
        self._pause_indefinitely.setEnabled_(snapshot.pause_enabled)
        self._resume.setEnabled_(snapshot.resume_enabled)
        return snapshot

    def stop(self) -> None:
        if self._status_item is None:
            return
        assert self._appkit is not None
        self._appkit.NSStatusBar.systemStatusBar().removeStatusItem_(self._status_item)
        self._status_item = None
        self._status_line = None
        self._pause_fifteen = None
        self._pause_indefinitely = None
        self._resume = None
        self._target = None

    def pause_for_fifteen_minutes(self) -> None:
        self._model.pause_for_fifteen_minutes()
        self.refresh()

    def pause_indefinitely(self) -> None:
        self._model.pause_indefinitely()
        self.refresh()

    def resume(self) -> None:
        self._model.resume()
        self.refresh()


def _menu_item(
    appkit: ModuleType,
    title: str,
    action: str | None,
    target: object,
) -> Any:
    item = appkit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        title,
        action,
        "",
    )
    item.setTarget_(target)
    return item


def _remove_partial_status_item(status_bar: Any, status_item: Any) -> bool:
    if status_bar is None or status_item is None:
        return False
    try:
        status_bar.removeStatusItem_(status_item)
    except Exception:
        return True
    return False


def _build_action_target(owner: NativeMenuBarController) -> Any:
    try:
        foundation = import_module("Foundation")
        objc = import_module("objc")
        ns_object = foundation.NSObject
    except (ImportError, AttributeError) as error:
        raise CollectorUnavailableError(
            "The macOS Objective-C bridge is unavailable"
        ) from error

    class ActionTarget(ns_object):  # type: ignore[misc, valid-type]
        def initWithOwner_(self, controller: NativeMenuBarController) -> Any:
            initialized = objc.super(ActionTarget, self).init()
            if initialized is None:
                return None
            initialized._contx_owner = controller
            return initialized

        def pauseFifteenMinutes_(self, _sender: object) -> None:
            self._contx_owner.pause_for_fifteen_minutes()

        def pauseIndefinitely_(self, _sender: object) -> None:
            self._contx_owner.pause_indefinitely()

        def resumeCollection_(self, _sender: object) -> None:
            self._contx_owner.resume()

    try:
        return cast(Any, ActionTarget).alloc().initWithOwner_(owner)
    except Exception as error:
        raise CollectorUnavailableError(
            "Cannot create the CONTX menu action target"
        ) from error


def _load_appkit() -> ModuleType:
    if sys.platform != "darwin":
        raise CollectorUnavailableError(
            "The menu-bar control is available only on macOS"
        )
    try:
        return import_module("AppKit")
    except ImportError as error:
        raise CollectorUnavailableError(
            "The macOS Cocoa bridge is not installed or unavailable"
        ) from error
