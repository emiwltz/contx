"""Non-prompting macOS collection capability detection."""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from importlib import import_module
from types import ModuleType

from contx.settings import CollectionSettings


class CapabilityStatus(StrEnum):
    """One truthful capability state that never implies permission was granted."""

    AVAILABLE = "available"
    DISABLED = "disabled"
    PERMISSION_REQUIRED = "permission_required"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class CollectionCapability:
    """Inspectable status for one collection feature."""

    name: str
    status: CapabilityStatus
    reason_code: str | None = None
    settings_path: str | None = None


ModuleLoader = Callable[[str], ModuleType]


def detect_collection_capabilities(
    settings: CollectionSettings,
    *,
    platform: str = sys.platform,
    module_loader: ModuleLoader = import_module,
) -> tuple[CollectionCapability, ...]:
    """Detect local APIs and existing permissions without requesting access."""
    if platform != "darwin":
        return tuple(
            CollectionCapability(
                name=name,
                status=CapabilityStatus.UNAVAILABLE,
                reason_code="macos_required",
            )
            for name in (
                "active_application",
                "system_notifications",
                "idle_detection",
                "window_titles",
                "screenshots",
            )
        )

    cocoa = _optional_module("AppKit", module_loader)
    quartz = _optional_module("Quartz", module_loader)
    cocoa_available = cocoa is not None and all(
        hasattr(cocoa, attribute)
        for attribute in (
            "NSWorkspace",
            "NSWorkspaceDidActivateApplicationNotification",
            "NSWorkspaceWillSleepNotification",
            "NSWorkspaceDidWakeNotification",
        )
    )
    quartz_idle_available = quartz is not None and all(
        hasattr(quartz, attribute)
        for attribute in (
            "CGEventSourceSecondsSinceLastEventType",
            "kCGEventSourceStateCombinedSessionState",
            "kCGAnyInputEventType",
        )
    )

    capabilities = [
        _api_capability("active_application", cocoa_available, "cocoa_bridge_missing"),
        _api_capability(
            "system_notifications", cocoa_available, "cocoa_bridge_missing"
        ),
        _api_capability(
            "idle_detection", quartz_idle_available, "quartz_bridge_missing"
        ),
        _permission_capability(
            name="window_titles",
            enabled=settings.window_titles_enabled,
            module=quartz,
            preflight_name="AXIsProcessTrusted",
            missing_api_reason="accessibility_preflight_unavailable",
            permission_reason="accessibility_permission_missing",
            settings_path="System Settings > Privacy & Security > Accessibility",
        ),
        _permission_capability(
            name="screenshots",
            enabled=settings.screenshots_enabled,
            module=quartz,
            preflight_name="CGPreflightScreenCaptureAccess",
            missing_api_reason="screen_capture_preflight_unavailable",
            permission_reason="screen_recording_permission_missing",
            settings_path=(
                "System Settings > Privacy & Security > Screen & System Audio Recording"
            ),
        ),
    ]
    return tuple(capabilities)


def _optional_module(name: str, loader: ModuleLoader) -> ModuleType | None:
    try:
        return loader(name)
    except (ImportError, AttributeError):
        return None


def _api_capability(
    name: str, available: bool, unavailable_reason: str
) -> CollectionCapability:
    return CollectionCapability(
        name=name,
        status=(
            CapabilityStatus.AVAILABLE if available else CapabilityStatus.UNAVAILABLE
        ),
        reason_code=None if available else unavailable_reason,
    )


def _permission_capability(
    *,
    name: str,
    enabled: bool,
    module: ModuleType | None,
    preflight_name: str,
    missing_api_reason: str,
    permission_reason: str,
    settings_path: str,
) -> CollectionCapability:
    if not enabled:
        return CollectionCapability(
            name=name,
            status=CapabilityStatus.DISABLED,
            reason_code="disabled_by_configuration",
        )
    if module is None or not hasattr(module, preflight_name):
        return CollectionCapability(
            name=name,
            status=CapabilityStatus.UNAVAILABLE,
            reason_code=missing_api_reason,
        )
    try:
        granted = bool(getattr(module, preflight_name)())
    except Exception:
        return CollectionCapability(
            name=name,
            status=CapabilityStatus.UNAVAILABLE,
            reason_code="permission_preflight_failed",
        )
    if not granted:
        return CollectionCapability(
            name=name,
            status=CapabilityStatus.PERMISSION_REQUIRED,
            reason_code=permission_reason,
            settings_path=settings_path,
        )
    return CollectionCapability(name=name, status=CapabilityStatus.AVAILABLE)
