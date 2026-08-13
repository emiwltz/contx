"""Explicit one-shot macOS permission requests for an authorized setup flow."""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from importlib import import_module
from types import ModuleType

from contx.errors import CollectorUnavailableError


class MacOSPermission(StrEnum):
    """Permissions that CONTX may request only through an explicit user action."""

    ACCESSIBILITY = "accessibility"
    SCREEN_RECORDING = "screen_recording"


@dataclass(frozen=True, slots=True)
class PermissionRequestResult:
    """Content-free result of one macOS permission request."""

    permission: MacOSPermission
    granted: bool
    settings_path: str


ModuleLoader = Callable[[str], ModuleType]


def request_collection_permission(
    permission: MacOSPermission,
    *,
    platform: str = sys.platform,
    module_loader: ModuleLoader = import_module,
) -> PermissionRequestResult:
    """Issue exactly one user-selected native request without reading activity."""
    if platform != "darwin":
        raise CollectorUnavailableError(
            "Collection permissions can be requested only on macOS"
        )
    if permission is MacOSPermission.ACCESSIBILITY:
        return _request_accessibility(module_loader)
    if permission is MacOSPermission.SCREEN_RECORDING:
        return _request_screen_recording(module_loader)
    raise CollectorUnavailableError("The requested macOS permission is unsupported")


def _request_accessibility(module_loader: ModuleLoader) -> PermissionRequestResult:
    try:
        accessibility = module_loader("ApplicationServices")
        request = accessibility.AXIsProcessTrustedWithOptions
        prompt_key = accessibility.kAXTrustedCheckOptionPrompt
    except (ImportError, AttributeError) as error:
        raise CollectorUnavailableError(
            "The macOS Accessibility permission API is unavailable"
        ) from error
    try:
        granted = bool(request({prompt_key: True}))
    except Exception as error:
        raise CollectorUnavailableError(
            "macOS could not request Accessibility permission"
        ) from error
    return PermissionRequestResult(
        permission=MacOSPermission.ACCESSIBILITY,
        granted=granted,
        settings_path="System Settings > Privacy & Security > Accessibility",
    )


def _request_screen_recording(module_loader: ModuleLoader) -> PermissionRequestResult:
    try:
        quartz = module_loader("Quartz")
        request = quartz.CGRequestScreenCaptureAccess
    except (ImportError, AttributeError) as error:
        raise CollectorUnavailableError(
            "The macOS Screen Recording permission API is unavailable"
        ) from error
    try:
        granted = bool(request())
    except Exception as error:
        raise CollectorUnavailableError(
            "macOS could not request Screen Recording permission"
        ) from error
    return PermissionRequestResult(
        permission=MacOSPermission.SCREEN_RECORDING,
        granted=granted,
        settings_path=(
            "System Settings > Privacy & Security > Screen & System Audio Recording"
        ),
    )
