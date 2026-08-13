"""Focused-window ScreenCaptureKit source with fail-closed context checks."""

from __future__ import annotations

import math
import sys
from collections.abc import Callable
from importlib import import_module
from threading import Event
from typing import Any, Protocol, cast

from contx.collection.continuous import ActivitySample
from contx.collectors.macos.activity import (
    ApplicationMetadata,
    ApplicationProbe,
    WorkspaceApplicationProbe,
)
from contx.errors import CollectorUnavailableError, ScreenshotCaptureSkipped
from contx.models import ActivityState

from .window_identity import WindowListApi, frontmost_window_id

PNG_UTI = "public.png"
DEFAULT_CAPTURE_TIMEOUT_SECONDS = 5.0
MAX_CAPTURE_DIMENSION = 8192
SHAREABLE_CONTENT_SELECTOR = (
    "getShareableContentExcludingDesktopWindows_onScreenWindowsOnly_completionHandler_"
)
CONTENT_FILTER_SELECTOR = "initWithDesktopIndependentWindow_"
CAPTURE_IMAGE_SELECTOR = "captureImageWithFilter_configuration_completionHandler_"


class FocusedWindowTitleReader(Protocol):
    def read(self) -> str | None: ...


class ScreenCaptureKitApi(WindowListApi, Protocol):
    SCShareableContent: Any
    SCContentFilter: Any
    SCStreamConfiguration: Any
    SCScreenshotManager: Any

    def CGPreflightScreenCaptureAccess(self) -> bool: ...

    def CFDataCreateMutable(
        self,
        allocator: object | None,
        capacity: int,
    ) -> Any: ...

    def CGImageDestinationCreateWithData(
        self,
        data: Any,
        type_identifier: str,
        image_count: int,
        options: object | None,
    ) -> object | None: ...

    def CGImageDestinationAddImage(
        self,
        destination: object,
        image: object,
        properties: object | None,
    ) -> None: ...

    def CGImageDestinationFinalize(self, destination: object) -> bool: ...


class ScreenCaptureKitScreenshotSource:
    """Capture only the policy-authorized frontmost application window."""

    def __init__(
        self,
        quartz: ScreenCaptureKitApi | None = None,
        *,
        application: ApplicationProbe | None = None,
        window_title: FocusedWindowTitleReader | None = None,
        timeout_seconds: float = DEFAULT_CAPTURE_TIMEOUT_SECONDS,
    ) -> None:
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("capture timeout must be a positive finite duration")
        self._quartz = quartz
        self._application = application or WorkspaceApplicationProbe()
        self._window_title = window_title
        self._timeout_seconds = timeout_seconds

    def capture_png(self, sample: ActivitySample) -> bytes:
        """Capture one exact window, discarding output if focus changes."""
        process_id = _required_process_id(sample)
        window_id = _required_window_id(sample)
        quartz = self._quartz or _load_quartz()
        _require_screen_capture_permission(quartz)
        self._assert_context_unchanged(sample, quartz, window_id)

        shareable_content_method = getattr(
            quartz.SCShareableContent,
            SHAREABLE_CONTENT_SELECTOR,
        )
        shareable_content = _await_result(
            lambda completion: shareable_content_method(
                True,
                True,
                completion,
            ),
            timeout_seconds=self._timeout_seconds,
            timeout_message="Timed out while resolving the focused macOS window",
            failure_message="macOS could not resolve shareable window metadata",
        )
        window = _matching_shareable_window(
            shareable_content,
            window_id=window_id,
            process_id=process_id,
            bundle_id=sample.app_bundle_id,
        )
        content_filter = getattr(
            quartz.SCContentFilter.alloc(),
            CONTENT_FILTER_SELECTOR,
        )(
            window,
        )
        if content_filter is None:
            raise CollectorUnavailableError(
                "macOS could not isolate the focused window for capture"
            )
        configuration = _build_configuration(quartz, content_filter)

        self._assert_context_unchanged(sample, quartz, window_id)
        capture_image_method = getattr(
            quartz.SCScreenshotManager,
            CAPTURE_IMAGE_SELECTOR,
        )
        image = _await_result(
            lambda completion: capture_image_method(
                content_filter,
                configuration,
                completion,
            ),
            timeout_seconds=self._timeout_seconds,
            timeout_message="Timed out while capturing the focused macOS window",
            failure_message="macOS could not capture the focused window",
        )
        self._assert_context_unchanged(sample, quartz, window_id)
        return _encode_png(quartz, image)

    def _assert_context_unchanged(
        self,
        sample: ActivitySample,
        quartz: ScreenCaptureKitApi,
        window_id: int,
    ) -> None:
        self._assert_application_unchanged(sample)
        if self._window_title is not None:
            try:
                current_title = self._window_title.read()
            except CollectorUnavailableError:
                raise
            except Exception as error:
                raise CollectorUnavailableError(
                    "Cannot revalidate the focused macOS window title"
                ) from error
            if current_title != sample.window_title:
                raise ScreenshotCaptureSkipped("focused_window_title_changed")
        current_window_id = frontmost_window_id(
            quartz,
            _required_process_id(sample),
        )
        if current_window_id != window_id:
            raise ScreenshotCaptureSkipped("focused_window_changed")

    def _assert_application_unchanged(self, sample: ActivitySample) -> None:
        try:
            current = self._application.read()
        except CollectorUnavailableError:
            raise
        except Exception as error:
            raise CollectorUnavailableError(
                "Cannot revalidate the frontmost macOS application"
            ) from error
        if not _same_application(sample, current):
            raise ScreenshotCaptureSkipped("frontmost_application_changed")


def _required_process_id(sample: ActivitySample) -> int:
    if sample.activity_state is not ActivityState.ACTIVE or sample.process_id is None:
        raise CollectorUnavailableError(
            "Focused-window capture requires an active sample with a process ID"
        )
    return sample.process_id


def _required_window_id(sample: ActivitySample) -> int:
    if sample.window_id is None:
        raise ScreenshotCaptureSkipped("no_authorized_focused_window")
    return sample.window_id


def _same_application(
    sample: ActivitySample,
    current: ApplicationMetadata,
) -> bool:
    if sample.process_id != current.process_id:
        return False
    if (
        sample.app_bundle_id is not None
        and sample.app_bundle_id != current.app_bundle_id
    ):
        return False
    return sample.app_name is None or sample.app_name == current.app_name


def _require_screen_capture_permission(quartz: ScreenCaptureKitApi) -> None:
    try:
        authorized = bool(quartz.CGPreflightScreenCaptureAccess())
    except Exception as error:
        raise CollectorUnavailableError(
            "Cannot verify macOS screen-capture permission"
        ) from error
    if not authorized:
        raise CollectorUnavailableError("macOS screen-capture permission is required")


def _matching_shareable_window(
    shareable_content: object,
    *,
    window_id: int,
    process_id: int,
    bundle_id: str | None,
) -> object:
    try:
        native_content: Any = shareable_content
        windows = native_content.windows()
    except Exception as error:
        raise CollectorUnavailableError(
            "macOS returned invalid shareable window metadata"
        ) from error
    for window in windows:
        try:
            owner = window.owningApplication()
            if owner is None:
                continue
            if int(window.windowID()) != window_id:
                continue
            if int(window.windowLayer()) != 0 or not bool(window.isOnScreen()):
                continue
            if int(owner.processID()) != process_id:
                continue
            if bundle_id is not None and str(owner.bundleIdentifier()) != bundle_id:
                continue
        except Exception:
            continue
        return window
    raise ScreenshotCaptureSkipped("focused_window_not_shareable")


def _build_configuration(quartz: ScreenCaptureKitApi, content_filter: Any) -> object:
    try:
        bounds = content_filter.contentRect()
        scale = float(content_filter.pointPixelScale())
        width_points, height_points = _rect_size(bounds)
        width, height = _bounded_pixel_size(
            width_points * scale,
            height_points * scale,
        )
        configuration = quartz.SCStreamConfiguration.alloc().init()
        configuration.setWidth_(width)
        configuration.setHeight_(height)
        configuration.setIgnoreShadowsSingleWindow_(True)
    except Exception as error:
        raise CollectorUnavailableError(
            "macOS could not configure the focused-window capture"
        ) from error
    if configuration is None:
        raise CollectorUnavailableError(
            "macOS could not create a focused-window capture configuration"
        )
    return configuration


def _rect_size(bounds: object) -> tuple[float, float]:
    size = getattr(bounds, "size", None)
    if size is None:
        raise ValueError("capture bounds have no size")
    native_size: Any = size
    width = float(native_size.width)
    height = float(native_size.height)
    if (
        not math.isfinite(width)
        or not math.isfinite(height)
        or width <= 0
        or height <= 0
    ):
        raise ValueError("capture bounds are invalid")
    return width, height


def _bounded_pixel_size(width: float, height: float) -> tuple[int, int]:
    if (
        not math.isfinite(width)
        or not math.isfinite(height)
        or width <= 0
        or height <= 0
    ):
        raise ValueError("capture pixel dimensions are invalid")
    ratio = min(1.0, MAX_CAPTURE_DIMENSION / max(width, height))
    return max(1, round(width * ratio)), max(1, round(height * ratio))


def _await_result(
    start: Callable[[Callable[[object | None, object | None], None]], None],
    *,
    timeout_seconds: float,
    timeout_message: str,
    failure_message: str,
) -> object:
    finished = Event()
    result: list[object | None] = [None]
    native_error: list[object | None] = [None]

    def completion(value: object | None, error: object | None) -> None:
        result[0] = value
        native_error[0] = error
        finished.set()

    try:
        start(completion)
    except Exception as error:
        raise CollectorUnavailableError(failure_message) from error
    if not finished.wait(timeout_seconds):
        raise CollectorUnavailableError(timeout_message)
    if native_error[0] is not None or result[0] is None:
        raise CollectorUnavailableError(failure_message)
    return result[0]


def _encode_png(quartz: ScreenCaptureKitApi, image: object) -> bytes:
    try:
        data = quartz.CFDataCreateMutable(None, 0)
        destination = quartz.CGImageDestinationCreateWithData(
            data,
            PNG_UTI,
            1,
            None,
        )
        if destination is None:
            raise CollectorUnavailableError(
                "macOS could not create an in-memory PNG destination"
            )
        quartz.CGImageDestinationAddImage(destination, image, None)
        if not quartz.CGImageDestinationFinalize(destination):
            raise CollectorUnavailableError(
                "macOS could not finalize the in-memory PNG"
            )
        payload = bytes(data)
    except CollectorUnavailableError:
        raise
    except Exception as error:
        raise CollectorUnavailableError(
            "Cannot encode the focused macOS window as PNG"
        ) from error
    if not payload:
        raise CollectorUnavailableError("macOS returned an empty PNG artifact")
    return payload


def _load_quartz() -> ScreenCaptureKitApi:
    if sys.platform != "darwin":
        raise CollectorUnavailableError("Screen capture is available only on macOS")
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
            "CGPreflightScreenCaptureAccess",
            "CGWindowListCopyWindowInfo",
            "SCShareableContent",
            "SCContentFilter",
            "SCStreamConfiguration",
            "SCScreenshotManager",
            "CFDataCreateMutable",
            "CGImageDestinationCreateWithData",
            "CGImageDestinationAddImage",
            "CGImageDestinationFinalize",
        )
        if not all(hasattr(quartz, name) for name in required):
            raise AttributeError
        required_selectors = (
            (quartz.SCShareableContent, SHAREABLE_CONTENT_SELECTOR),
            (quartz.SCContentFilter, CONTENT_FILTER_SELECTOR),
            (quartz.SCContentFilter, "contentRect"),
            (quartz.SCContentFilter, "pointPixelScale"),
            (quartz.SCStreamConfiguration, "alloc"),
            (quartz.SCStreamConfiguration, "setWidth_"),
            (quartz.SCStreamConfiguration, "setHeight_"),
            (quartz.SCStreamConfiguration, "setIgnoreShadowsSingleWindow_"),
            (quartz.SCScreenshotManager, CAPTURE_IMAGE_SELECTOR),
        )
        if not all(
            hasattr(native_type, selector)
            for native_type, selector in required_selectors
        ):
            raise AttributeError
    except (ImportError, AttributeError) as error:
        raise CollectorUnavailableError(
            "The required macOS ScreenCaptureKit APIs are unavailable"
        ) from error
    return cast(ScreenCaptureKitApi, quartz)
