"""Non-prompting Accessibility probe for the focused macOS window title."""

from __future__ import annotations

import sys
from importlib import import_module
from typing import Protocol, cast

from contx.errors import CollectorUnavailableError


class AccessibilityApi(Protocol):
    kAXFocusedApplicationAttribute: str
    kAXFocusedWindowAttribute: str
    kAXTitleAttribute: str
    kAXErrorSuccess: int
    kAXErrorNoValue: int
    kAXErrorAttributeUnsupported: int

    def AXIsProcessTrusted(self) -> bool: ...

    def AXUIElementCreateSystemWide(self) -> object: ...

    def AXUIElementCopyAttributeValue(
        self,
        element: object,
        attribute: str,
        value: None,
    ) -> tuple[int, object | None]: ...


class FocusedWindowTitleProbe:
    """Read one title only when Accessibility access is already trusted."""

    def __init__(self, accessibility: AccessibilityApi | None = None) -> None:
        self._accessibility = accessibility

    def read(self) -> str | None:
        accessibility = self._accessibility or _load_accessibility()
        try:
            trusted = bool(accessibility.AXIsProcessTrusted())
        except Exception as error:
            raise CollectorUnavailableError(
                "Cannot verify macOS Accessibility permission"
            ) from error
        if not trusted:
            raise CollectorUnavailableError(
                "macOS Accessibility permission is required for window titles"
            )
        try:
            system = accessibility.AXUIElementCreateSystemWide()
            application = _copy_optional(
                accessibility,
                system,
                accessibility.kAXFocusedApplicationAttribute,
            )
            if application is None:
                return None
            window = _copy_optional(
                accessibility,
                application,
                accessibility.kAXFocusedWindowAttribute,
            )
            if window is None:
                return None
            title = _copy_optional(
                accessibility,
                window,
                accessibility.kAXTitleAttribute,
            )
        except CollectorUnavailableError:
            raise
        except Exception as error:
            raise CollectorUnavailableError(
                "Cannot read the focused macOS window title"
            ) from error
        if title is None:
            return None
        if not isinstance(title, str):
            raise CollectorUnavailableError(
                "macOS returned an invalid focused-window title"
            )
        normalized = title.strip()
        return normalized or None


def _copy_optional(
    accessibility: AccessibilityApi,
    element: object,
    attribute: str,
) -> object | None:
    try:
        error_code, value = accessibility.AXUIElementCopyAttributeValue(
            element,
            attribute,
            None,
        )
    except Exception as error:
        raise CollectorUnavailableError(
            "The macOS Accessibility attribute read failed"
        ) from error
    if error_code == accessibility.kAXErrorSuccess:
        return value
    if error_code in {
        accessibility.kAXErrorNoValue,
        accessibility.kAXErrorAttributeUnsupported,
    }:
        return None
    raise CollectorUnavailableError(
        "macOS could not provide a required Accessibility attribute"
    )


def _load_accessibility() -> AccessibilityApi:
    if sys.platform != "darwin":
        raise CollectorUnavailableError(
            "Window-title access is available only on macOS"
        )
    try:
        accessibility = import_module("ApplicationServices")
        required = (
            "AXIsProcessTrusted",
            "AXUIElementCreateSystemWide",
            "AXUIElementCopyAttributeValue",
            "kAXFocusedApplicationAttribute",
            "kAXFocusedWindowAttribute",
            "kAXTitleAttribute",
            "kAXErrorSuccess",
            "kAXErrorNoValue",
            "kAXErrorAttributeUnsupported",
        )
        if not all(hasattr(accessibility, name) for name in required):
            raise AttributeError
    except (ImportError, AttributeError) as error:
        raise CollectorUnavailableError(
            "The required macOS Accessibility APIs are unavailable"
        ) from error
    return cast(AccessibilityApi, accessibility)
