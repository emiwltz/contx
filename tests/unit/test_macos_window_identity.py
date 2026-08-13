"""Content-free focused-window identity selection tests."""

import pytest

from contx.collectors.macos import CoreGraphicsFocusedWindowProbe
from contx.errors import CollectorUnavailableError


class FakeQuartz:
    kCGWindowListOptionOnScreenOnly = 1
    kCGWindowListExcludeDesktopElements = 16
    kCGNullWindowID = 0
    kCGWindowNumber = "number"
    kCGWindowLayer = "layer"
    kCGWindowAlpha = "alpha"
    kCGWindowOwnerPID = "pid"
    kCGWindowBounds = "bounds"

    def __init__(self, windows: tuple[dict[str, object], ...] | None) -> None:
        self.windows = windows
        self.arguments: tuple[int, int] | None = None

    def CGWindowListCopyWindowInfo(
        self,
        list_option: int,
        relative_to_window: int,
    ) -> tuple[dict[str, object], ...] | None:
        self.arguments = (list_option, relative_to_window)
        return self.windows


def test_probe_selects_first_visible_normal_window_for_process() -> None:
    quartz = FakeQuartz(
        (
            _window(10, process_id=9999),
            _window(11, layer=1),
            _window(12, alpha=0.0),
            _window(13, width=0),
            _window(77),
            _window(88),
        )
    )

    window_id = CoreGraphicsFocusedWindowProbe(quartz).read(process_id=4242)

    assert window_id == 77
    assert quartz.arguments == (17, 0)


def test_probe_returns_none_when_process_has_no_normal_window() -> None:
    quartz = FakeQuartz((_window(11, process_id=9999), _window(12, layer=1)))

    assert CoreGraphicsFocusedWindowProbe(quartz).read(process_id=4242) is None


def test_probe_reports_missing_window_server_and_rejects_invalid_pid() -> None:
    with pytest.raises(CollectorUnavailableError, match="window list"):
        CoreGraphicsFocusedWindowProbe(FakeQuartz(None)).read(process_id=4242)

    with pytest.raises(ValueError, match="positive"):
        CoreGraphicsFocusedWindowProbe(FakeQuartz(())).read(process_id=0)


def _window(
    window_id: int,
    *,
    process_id: int = 4242,
    layer: int = 0,
    alpha: float = 1.0,
    width: int = 1200,
    height: int = 800,
) -> dict[str, object]:
    return {
        "number": window_id,
        "layer": layer,
        "alpha": alpha,
        "pid": process_id,
        "bounds": {"Width": width, "Height": height},
    }
