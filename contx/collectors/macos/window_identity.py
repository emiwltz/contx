"""Content-free CoreGraphics identity for the focused application window."""

from __future__ import annotations

import math
import sys
from collections.abc import Mapping, Sequence
from importlib import import_module
from typing import Any, Protocol, cast

from contx.errors import CollectorUnavailableError


class WindowListApi(Protocol):
    kCGWindowListOptionOnScreenOnly: int
    kCGWindowListExcludeDesktopElements: int
    kCGNullWindowID: int
    kCGWindowNumber: str
    kCGWindowLayer: str
    kCGWindowAlpha: str
    kCGWindowOwnerPID: str
    kCGWindowBounds: str

    def CGWindowListCopyWindowInfo(
        self,
        list_option: int,
        relative_to_window: int,
    ) -> Sequence[Mapping[str, object]] | None: ...


class FocusedWindowIdentifierProbe(Protocol):
    """Read a content-free on-screen window ID for one process."""

    def read(self, *, process_id: int) -> int | None: ...


class CoreGraphicsFocusedWindowProbe:
    """Resolve the frontmost normal window owned by an authorized process."""

    def __init__(self, quartz: WindowListApi | None = None) -> None:
        self._quartz = quartz

    def read(self, *, process_id: int) -> int | None:
        if process_id <= 0:
            raise ValueError("process ID must be positive")
        return frontmost_window_id(self._quartz or _load_quartz(), process_id)


def frontmost_window_id(quartz: WindowListApi, process_id: int) -> int | None:
    """Return the first normal window in Apple's front-to-back list."""
    options = (
        quartz.kCGWindowListOptionOnScreenOnly
        | quartz.kCGWindowListExcludeDesktopElements
    )
    try:
        windows = quartz.CGWindowListCopyWindowInfo(
            options,
            quartz.kCGNullWindowID,
        )
    except Exception as error:
        raise CollectorUnavailableError(
            "Cannot resolve the focused macOS window"
        ) from error
    if windows is None:
        raise CollectorUnavailableError("macOS did not return an on-screen window list")
    for window in windows:
        try:
            native_window = cast(Mapping[str, Any], window)
            if int(native_window[quartz.kCGWindowOwnerPID]) != process_id:
                continue
            if int(native_window[quartz.kCGWindowLayer]) != 0:
                continue
            if float(native_window[quartz.kCGWindowAlpha]) <= 0:
                continue
            if not _has_visible_bounds(native_window[quartz.kCGWindowBounds]):
                continue
            window_id = int(native_window[quartz.kCGWindowNumber])
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        if window_id > 0:
            return window_id
    return None


def _has_visible_bounds(raw_bounds: object) -> bool:
    if not isinstance(raw_bounds, Mapping):
        return False
    try:
        width = float(raw_bounds["Width"])
        height = float(raw_bounds["Height"])
    except (KeyError, TypeError, ValueError, OverflowError):
        return False
    return math.isfinite(width) and math.isfinite(height) and width > 0 and height > 0


def _load_quartz() -> WindowListApi:
    if sys.platform != "darwin":
        raise CollectorUnavailableError(
            "Focused-window identification is available only on macOS"
        )
    try:
        quartz = import_module("Quartz")
        required = (
            "kCGWindowListOptionOnScreenOnly",
            "kCGWindowListExcludeDesktopElements",
            "kCGNullWindowID",
            "kCGWindowNumber",
            "kCGWindowLayer",
            "kCGWindowAlpha",
            "kCGWindowOwnerPID",
            "kCGWindowBounds",
            "CGWindowListCopyWindowInfo",
        )
        if not all(hasattr(quartz, name) for name in required):
            raise AttributeError
    except (ImportError, AttributeError) as error:
        raise CollectorUnavailableError(
            "The required macOS window-list APIs are unavailable"
        ) from error
    return cast(WindowListApi, quartz)
