"""Non-visible AppKit controller for a collector owned by CONTX.app."""

from __future__ import annotations

import sys
from importlib import import_module
from types import ModuleType
from typing import Any

from contx.errors import CollectorUnavailableError


class NativeHostChildController:
    """Keep the child AppKit loop accessory-only without a second status item."""

    def __init__(self, *, appkit: ModuleType | None = None) -> None:
        self._appkit = appkit
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        appkit = self._appkit or _load_appkit()
        try:
            application: Any = appkit.NSApplication.sharedApplication()
            accessory_policy = appkit.NSApplicationActivationPolicyAccessory
            application.setActivationPolicy_(accessory_policy)
            if application.activationPolicy() != accessory_policy:
                raise CollectorUnavailableError(
                    "macOS rejected the native-host child accessory policy"
                )
        except CollectorUnavailableError:
            raise
        except Exception as error:
            raise CollectorUnavailableError(
                "Cannot configure the native-host collector child"
            ) from error
        self._appkit = appkit
        self._started = True

    def refresh(self) -> None:
        if not self._started:
            raise RuntimeError("native-host child controller is not started")

    def stop(self) -> None:
        self._started = False


def _load_appkit() -> ModuleType:
    if sys.platform != "darwin":
        raise CollectorUnavailableError(
            "The native-host child controller is available only on macOS"
        )
    try:
        return import_module("AppKit")
    except ImportError as error:
        raise CollectorUnavailableError(
            "The macOS Cocoa bridge is not installed or unavailable"
        ) from error
