"""Permission-gated Quartz screenshot source with in-memory PNG encoding."""

from __future__ import annotations

import sys
from importlib import import_module
from typing import Any, Protocol, cast

from contx.errors import CollectorUnavailableError

PNG_UTI = "public.png"


class QuartzScreenshotApi(Protocol):
    CGRectInfinite: object
    kCGWindowListOptionOnScreenOnly: int
    kCGNullWindowID: int
    kCGWindowImageDefault: int

    def CGPreflightScreenCaptureAccess(self) -> bool: ...

    def CGWindowListCreateImage(
        self,
        screen_bounds: object,
        list_option: int,
        window_id: int,
        image_option: int,
    ) -> object | None: ...

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


class QuartzScreenshotSource:
    """Capture all visible displays only when access is already authorized."""

    def __init__(self, quartz: QuartzScreenshotApi | None = None) -> None:
        self._quartz = quartz

    def capture_png(self) -> bytes:
        quartz = self._quartz or _load_quartz()
        try:
            authorized = bool(quartz.CGPreflightScreenCaptureAccess())
        except Exception as error:
            raise CollectorUnavailableError(
                "Cannot verify macOS screen-capture permission"
            ) from error
        if not authorized:
            raise CollectorUnavailableError(
                "macOS screen-capture permission is required"
            )
        try:
            image = quartz.CGWindowListCreateImage(
                quartz.CGRectInfinite,
                quartz.kCGWindowListOptionOnScreenOnly,
                quartz.kCGNullWindowID,
                quartz.kCGWindowImageDefault,
            )
            if image is None:
                raise CollectorUnavailableError("macOS did not return a screen image")
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
                "Cannot capture and encode the macOS screen"
            ) from error
        if not payload:
            raise CollectorUnavailableError("macOS returned an empty PNG artifact")
        return payload


def _load_quartz() -> QuartzScreenshotApi:
    if sys.platform != "darwin":
        raise CollectorUnavailableError("Screen capture is available only on macOS")
    try:
        quartz = import_module("Quartz")
        required = (
            "CGRectInfinite",
            "kCGWindowListOptionOnScreenOnly",
            "kCGNullWindowID",
            "kCGWindowImageDefault",
            "CGPreflightScreenCaptureAccess",
            "CGWindowListCreateImage",
            "CFDataCreateMutable",
            "CGImageDestinationCreateWithData",
            "CGImageDestinationAddImage",
            "CGImageDestinationFinalize",
        )
        if not all(hasattr(quartz, name) for name in required):
            raise AttributeError
    except (ImportError, AttributeError) as error:
        raise CollectorUnavailableError(
            "The required macOS Quartz screen-capture APIs are unavailable"
        ) from error
    return cast(QuartzScreenshotApi, quartz)
