"""Focused-window ScreenCaptureKit permission and race boundaries."""

from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from contx.collection import ActivitySample
from contx.collectors.macos import (
    ApplicationMetadata,
    ScreenCaptureKitScreenshotSource,
)
from contx.errors import CollectorUnavailableError, ScreenshotCaptureSkipped
from contx.models import ActivityState

PNG = b"\x89PNG\r\n\x1a\nsynthetic-focused-window-fixture"
NOW = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)


class SequenceApplicationProbe:
    def __init__(self, *values: ApplicationMetadata) -> None:
        self._values = list(values)
        self.calls = 0

    def read(self) -> ApplicationMetadata:
        value = self._values[min(self.calls, len(self._values) - 1)]
        self.calls += 1
        return value


class SequenceTitleReader:
    def __init__(self, *values: str | None) -> None:
        self._values = list(values)
        self.calls = 0

    def read(self) -> str | None:
        value = self._values[min(self.calls, len(self._values) - 1)]
        self.calls += 1
        return value


class FakeOwner:
    def __init__(self, process_id: int, bundle_id: str) -> None:
        self._process_id = process_id
        self._bundle_id = bundle_id

    def processID(self) -> int:
        return self._process_id

    def bundleIdentifier(self) -> str:
        return self._bundle_id


class FakeWindow:
    def __init__(
        self,
        window_id: int,
        *,
        process_id: int = 4242,
        bundle_id: str = "com.example.editor",
        layer: int = 0,
        on_screen: bool = True,
    ) -> None:
        self._window_id = window_id
        self._owner = FakeOwner(process_id, bundle_id)
        self._layer = layer
        self._on_screen = on_screen

    def windowID(self) -> int:
        return self._window_id

    def owningApplication(self) -> FakeOwner:
        return self._owner

    def windowLayer(self) -> int:
        return self._layer

    def isOnScreen(self) -> bool:
        return self._on_screen


class FakeShareableContent:
    def __init__(self, windows: tuple[FakeWindow, ...]) -> None:
        self._windows = windows

    def windows(self) -> tuple[FakeWindow, ...]:
        return self._windows


class FakeShareableContentApi:
    def __init__(self, quartz: "FakeQuartz") -> None:
        self._quartz = quartz

    def getShareableContentExcludingDesktopWindows_onScreenWindowsOnly_completionHandler_(  # noqa: E501
        self,
        exclude_desktop: bool,
        on_screen_only: bool,
        completion: object,
    ) -> None:
        self._quartz.calls.append("shareable")
        assert exclude_desktop
        assert on_screen_only
        if not self._quartz.finish_shareable:
            return
        completion(self._quartz.shareable_content, self._quartz.shareable_error)


@dataclass
class FakeSize:
    width: float
    height: float


@dataclass
class FakeRect:
    size: FakeSize


class FakeContentFilter:
    def __init__(self, quartz: "FakeQuartz") -> None:
        self._quartz = quartz
        self.window: FakeWindow | None = None

    def initWithDesktopIndependentWindow_(
        self,
        window: FakeWindow,
    ) -> "FakeContentFilter":
        self.window = window
        self._quartz.calls.append("filter")
        return self

    def contentRect(self) -> FakeRect:
        return FakeRect(FakeSize(*self._quartz.content_size))

    def pointPixelScale(self) -> float:
        return self._quartz.scale


class FakeContentFilterApi:
    def __init__(self, quartz: "FakeQuartz") -> None:
        self._quartz = quartz

    def alloc(self) -> FakeContentFilter:
        return FakeContentFilter(self._quartz)


class FakeConfiguration:
    def __init__(self, quartz: "FakeQuartz") -> None:
        self._quartz = quartz

    def init(self) -> "FakeConfiguration":
        return self

    def setWidth_(self, width: int) -> None:
        self._quartz.config["width"] = width

    def setHeight_(self, height: int) -> None:
        self._quartz.config["height"] = height

    def setIgnoreShadowsSingleWindow_(self, value: bool) -> None:
        self._quartz.config["ignore_shadows"] = value


class FakeConfigurationApi:
    def __init__(self, quartz: "FakeQuartz") -> None:
        self._quartz = quartz

    def alloc(self) -> FakeConfiguration:
        return FakeConfiguration(self._quartz)


class FakeScreenshotManager:
    def __init__(self, quartz: "FakeQuartz") -> None:
        self._quartz = quartz

    def captureImageWithFilter_configuration_completionHandler_(
        self,
        content_filter: FakeContentFilter,
        configuration: FakeConfiguration,
        completion: object,
    ) -> None:
        self._quartz.calls.append("capture")
        assert content_filter.window is not None
        assert configuration is not None
        if not self._quartz.finish_capture:
            return
        completion(self._quartz.image, self._quartz.capture_error)


class FakeQuartz:
    kCGWindowListOptionOnScreenOnly = 1
    kCGWindowListExcludeDesktopElements = 16
    kCGNullWindowID = 0
    kCGWindowNumber = "number"
    kCGWindowLayer = "layer"
    kCGWindowAlpha = "alpha"
    kCGWindowOwnerPID = "pid"
    kCGWindowBounds = "bounds"

    def __init__(
        self,
        *,
        authorized: bool = True,
        window_ids: tuple[int, ...] = (77,),
        shareable_windows: tuple[FakeWindow, ...] = (FakeWindow(77),),
    ) -> None:
        self.authorized = authorized
        self.window_ids = window_ids
        self.window_list_calls = 0
        self.shareable_content = FakeShareableContent(shareable_windows)
        self.shareable_error: object | None = None
        self.capture_error: object | None = None
        self.finish_shareable = True
        self.finish_capture = True
        self.image: object | None = object()
        self.content_size = (1200.0, 800.0)
        self.scale = 2.0
        self.calls: list[str] = []
        self.config: dict[str, int | bool] = {}
        self.data = bytearray()
        self.SCShareableContent = FakeShareableContentApi(self)
        self.SCContentFilter = FakeContentFilterApi(self)
        self.SCStreamConfiguration = FakeConfigurationApi(self)
        self.SCScreenshotManager = FakeScreenshotManager(self)

    def CGPreflightScreenCaptureAccess(self) -> bool:
        self.calls.append("preflight")
        return self.authorized

    def CGWindowListCopyWindowInfo(
        self,
        list_option: int,
        relative_to_window: int,
    ) -> tuple[dict[str, object], ...]:
        self.calls.append("window_list")
        assert list_option == 17
        assert relative_to_window == 0
        window_id = self.window_ids[
            min(self.window_list_calls, len(self.window_ids) - 1)
        ]
        self.window_list_calls += 1
        return (
            self._window(11, process_id=9000),
            self._window(12, layer=1),
            self._window(13, alpha=0.0),
            self._window(window_id),
            self._window(88),
        )

    def _window(
        self,
        window_id: int,
        *,
        process_id: int = 4242,
        layer: int = 0,
        alpha: float = 1.0,
    ) -> dict[str, object]:
        return {
            "number": window_id,
            "layer": layer,
            "alpha": alpha,
            "pid": process_id,
            "bounds": {"Width": 1200, "Height": 800},
        }

    def CFDataCreateMutable(
        self,
        allocator: object | None,
        capacity: int,
    ) -> bytearray:
        self.calls.append("data")
        assert allocator is None
        assert capacity == 0
        return self.data

    def CGImageDestinationCreateWithData(
        self,
        data: bytearray,
        type_identifier: str,
        image_count: int,
        options: object | None,
    ) -> object:
        self.calls.append("destination")
        assert data is self.data
        assert type_identifier == "public.png"
        assert image_count == 1
        assert options is None
        return object()

    def CGImageDestinationAddImage(
        self,
        destination: object,
        image: object,
        properties: object | None,
    ) -> None:
        self.calls.append("encode")
        assert destination is not None
        assert image is self.image
        assert properties is None
        self.data.extend(PNG)

    def CGImageDestinationFinalize(self, destination: object) -> bool:
        self.calls.append("finalize")
        assert destination is not None
        return True


def test_missing_permission_prevents_window_or_pixel_reads() -> None:
    quartz = FakeQuartz(authorized=False)

    with pytest.raises(CollectorUnavailableError, match="permission is required"):
        _source(quartz).capture_png(_sample())

    assert quartz.calls == ["preflight"]


def test_sample_without_an_authorized_window_never_preflights_or_reads_pixels() -> None:
    quartz = FakeQuartz()

    with pytest.raises(
        ScreenshotCaptureSkipped,
        match="no_authorized_focused_window",
    ):
        _source(quartz).capture_png(_sample(window_id=None))

    assert quartz.calls == []


def test_same_process_window_change_before_resolution_fails_closed() -> None:
    quartz = FakeQuartz(window_ids=(88,))

    with pytest.raises(ScreenshotCaptureSkipped, match="focused_window_changed"):
        _source(quartz).capture_png(_sample(window_id=77))

    assert quartz.calls == ["preflight", "window_list"]


def test_authorized_capture_isolates_one_frontmost_window() -> None:
    quartz = FakeQuartz()

    payload = _source(quartz).capture_png(_sample())

    assert payload == PNG
    assert quartz.calls == [
        "preflight",
        "window_list",
        "shareable",
        "filter",
        "window_list",
        "capture",
        "window_list",
        "data",
        "destination",
        "encode",
        "finalize",
    ]
    assert quartz.config == {
        "width": 2400,
        "height": 1600,
        "ignore_shadows": True,
    }


def test_application_switch_before_capture_prevents_pixel_read() -> None:
    quartz = FakeQuartz()
    changed = ApplicationMetadata("Private App", "com.example.private", 9000)
    application = SequenceApplicationProbe(_metadata(), changed)

    with pytest.raises(
        ScreenshotCaptureSkipped,
        match="frontmost_application_changed",
    ):
        _source(quartz, application=application).capture_png(_sample())

    assert "capture" not in quartz.calls


def test_window_switch_before_capture_prevents_pixel_read() -> None:
    quartz = FakeQuartz(window_ids=(77, 88))

    with pytest.raises(ScreenshotCaptureSkipped, match="focused_window_changed"):
        _source(quartz).capture_png(_sample())

    assert "capture" not in quartz.calls


def test_title_switch_before_capture_prevents_pixel_read() -> None:
    quartz = FakeQuartz()
    titles = SequenceTitleReader("Private Checkout")

    with pytest.raises(
        ScreenshotCaptureSkipped,
        match="focused_window_title_changed",
    ):
        _source(quartz, title=titles).capture_png(_sample(title="Project Atlas"))

    assert "capture" not in quartz.calls


def test_context_switch_during_capture_discards_the_pixels() -> None:
    quartz = FakeQuartz(window_ids=(77, 77, 88))

    with pytest.raises(ScreenshotCaptureSkipped, match="focused_window_changed"):
        _source(quartz).capture_png(_sample())

    assert "capture" in quartz.calls
    assert "data" not in quartz.calls


def test_non_shareable_or_timed_out_window_fails_closed() -> None:
    quartz = FakeQuartz(shareable_windows=(FakeWindow(88),))
    with pytest.raises(
        ScreenshotCaptureSkipped,
        match="focused_window_not_shareable",
    ):
        _source(quartz).capture_png(_sample())
    assert "capture" not in quartz.calls

    timeout = FakeQuartz()
    timeout.finish_shareable = False
    with pytest.raises(CollectorUnavailableError, match="Timed out"):
        _source(timeout, timeout_seconds=0.001).capture_png(_sample())
    assert "capture" not in timeout.calls


def test_large_retina_window_is_bounded_without_changing_aspect_ratio() -> None:
    quartz = FakeQuartz()
    quartz.content_size = (8000.0, 4000.0)
    quartz.scale = 2.0

    _source(quartz).capture_png(_sample())

    assert quartz.config["width"] == 8192
    assert quartz.config["height"] == 4096


def _source(
    quartz: FakeQuartz,
    *,
    application: SequenceApplicationProbe | None = None,
    title: SequenceTitleReader | None = None,
    timeout_seconds: float = 1.0,
) -> ScreenCaptureKitScreenshotSource:
    return ScreenCaptureKitScreenshotSource(
        quartz,
        application=application or SequenceApplicationProbe(_metadata()),
        window_title=title,
        timeout_seconds=timeout_seconds,
    )


def _sample(
    *,
    title: str | None = None,
    window_id: int | None = 77,
) -> ActivitySample:
    return ActivitySample(
        observed_at=NOW,
        activity_state=ActivityState.ACTIVE,
        app_name="Synthetic Editor",
        app_bundle_id="com.example.editor",
        window_title=title,
        process_id=4242,
        window_id=window_id,
    )


def _metadata() -> ApplicationMetadata:
    return ApplicationMetadata("Synthetic Editor", "com.example.editor", 4242)
