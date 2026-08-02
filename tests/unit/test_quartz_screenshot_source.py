"""Quartz screenshot source permission and in-memory encoding boundaries."""

import pytest

from contx.collectors.macos import QuartzScreenshotSource
from contx.errors import CollectorUnavailableError

PNG = b"\x89PNG\r\n\x1a\nsynthetic-quartz-fixture"


class FakeQuartz:
    CGRectInfinite = object()
    kCGWindowListOptionOnScreenOnly = 1
    kCGNullWindowID = 0
    kCGWindowImageDefault = 0

    def __init__(
        self,
        *,
        authorized: bool = True,
        image: object | None = object(),
        finalize: bool = True,
    ) -> None:
        self.authorized = authorized
        self.image = image
        self.finalize = finalize
        self.calls: list[str] = []
        self.data = bytearray()

    def CGPreflightScreenCaptureAccess(self) -> bool:
        self.calls.append("preflight")
        return self.authorized

    def CGWindowListCreateImage(
        self,
        screen_bounds: object,
        list_option: int,
        window_id: int,
        image_option: int,
    ) -> object | None:
        self.calls.append("capture")
        assert screen_bounds is self.CGRectInfinite
        assert list_option == self.kCGWindowListOptionOnScreenOnly
        assert window_id == self.kCGNullWindowID
        assert image_option == self.kCGWindowImageDefault
        return self.image

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
        return self.finalize


def test_missing_permission_prevents_any_pixel_read() -> None:
    quartz = FakeQuartz(authorized=False)

    with pytest.raises(CollectorUnavailableError, match="permission is required"):
        QuartzScreenshotSource(quartz).capture_png()

    assert quartz.calls == ["preflight"]


def test_authorized_capture_encodes_one_in_memory_png() -> None:
    quartz = FakeQuartz()

    payload = QuartzScreenshotSource(quartz).capture_png()

    assert payload == PNG
    assert quartz.calls == [
        "preflight",
        "capture",
        "data",
        "destination",
        "encode",
        "finalize",
    ]


def test_missing_image_and_failed_encoding_are_actionable() -> None:
    with pytest.raises(CollectorUnavailableError, match="did not return"):
        QuartzScreenshotSource(FakeQuartz(image=None)).capture_png()

    with pytest.raises(CollectorUnavailableError, match="could not finalize"):
        QuartzScreenshotSource(FakeQuartz(finalize=False)).capture_png()
