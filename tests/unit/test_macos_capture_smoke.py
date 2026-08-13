"""Content-free helpers for the host-dependent focused-window smoke."""

import stat
import struct
from collections.abc import Callable
from pathlib import Path

import pytest

from contx.errors import CollectorUnavailableError
from contx.evaluation.macos_capture_smoke import (
    _SyntheticWindowHost,
    png_dimensions,
    validate_private_output_path,
    write_private_png,
)

PNG = (
    b"\x89PNG\r\n\x1a\n"
    + struct.pack(">I", 13)
    + b"IHDR"
    + struct.pack(">II", 640, 400)
    + b"\x08\x06\x00\x00\x00"
)


def test_png_dimensions_read_only_the_ihdr_boundary() -> None:
    assert png_dimensions(PNG) == (640, 400)


@pytest.mark.parametrize(
    "payload",
    (
        b"",
        b"not a png" + (b"\x00" * 30),
        b"\x89PNG\r\n\x1a\n" + (b"\x00" * 16),
    ),
)
def test_invalid_png_header_is_actionable(payload: bytes) -> None:
    with pytest.raises(CollectorUnavailableError, match="PNG"):
        png_dimensions(payload)


def test_private_writer_is_exclusive_and_uses_mode_0600(tmp_path: Path) -> None:
    target = tmp_path / "focused-window.png"

    written = write_private_png(target, PNG)

    assert written == target
    assert written.read_bytes() == PNG
    assert stat.S_IMODE(written.stat().st_mode) == 0o600
    with pytest.raises(ValueError, match="already exists"):
        write_private_png(target, PNG)


def test_private_writer_requires_an_absolute_png_path(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="absolute"):
        write_private_png(Path("relative.png"), PNG)
    with pytest.raises(ValueError, match="end in .png"):
        write_private_png(tmp_path / "capture.jpg", PNG)


def test_output_validation_rejects_existing_target_before_capture(
    tmp_path: Path,
) -> None:
    target = tmp_path / "existing.png"
    target.write_bytes(b"existing")

    with pytest.raises(ValueError, match="already exists"):
        validate_private_output_path(target)


def test_appkit_host_runs_and_wakes_real_application_loop() -> None:
    appkit = _FakeAppKit()
    scheduled: list[tuple[float, Callable[[], None]]] = []

    def call_later(delay: float, callback: Callable[[], None]) -> None:
        scheduled.append((delay, callback))
        appkit.application.delayed_callback = callback

    host = _SyntheticWindowHost(
        appkit,  # type: ignore[arg-type]
        _FakeFoundation(),  # type: ignore[arg-type]
        call_later=call_later,
        focus_settle_seconds=0,
        activation_poll_seconds=0,
    )

    host.start()
    result = host.run_while_active(lambda: "workflow-result")

    assert result == "workflow-result"
    assert len(scheduled) == 1
    assert scheduled[0][0] > 0
    assert appkit.application.calls == [
        "policy",
        "activate_legacy",
        "finish_launching",
        "activate_legacy",
        "run",
        "activate_legacy",
        "stop",
        "post_event",
    ]
    assert appkit.application.posted_event is appkit.event
    assert appkit.application.posted_at_start is True
    host.close()


def test_appkit_host_rejects_invalid_durations() -> None:
    for invalid in (-1.0, float("nan"), float("inf")):
        with pytest.raises(ValueError, match="finite and non-negative"):
            _SyntheticWindowHost(
                _FakeAppKit(),  # type: ignore[arg-type]
                _FakeFoundation(),  # type: ignore[arg-type]
                focus_settle_seconds=invalid,
            )


class _FakeApplication:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.delayed_callback: Callable[[], None] | None = None
        self.posted_event: object | None = None
        self.posted_at_start: bool | None = None

    def setActivationPolicy_(self, _policy: int) -> bool:
        self.calls.append("policy")
        return True

    def activateIgnoringOtherApps_(self, _enabled: bool) -> None:
        self.calls.append("activate_legacy")

    def finishLaunching(self) -> None:
        self.calls.append("finish_launching")

    def run(self) -> None:
        self.calls.append("run")
        assert callable(self.delayed_callback)
        self.delayed_callback()

    def stop_(self, _sender: object | None) -> None:
        self.calls.append("stop")

    def postEvent_atStart_(self, event: object, at_start: bool) -> None:
        self.calls.append("post_event")
        self.posted_event = event
        self.posted_at_start = at_start

    def isActive(self) -> bool:
        return True

    def deactivate(self) -> None:
        self.calls.append("deactivate")


class _FakeWindow:
    def __init__(self) -> None:
        self.content_view = _FakeContentView()

    def setReleasedWhenClosed_(self, _released: bool) -> None:
        pass

    def setTitle_(self, _title: str) -> None:
        pass

    def setBackgroundColor_(self, _color: object) -> None:
        pass

    def contentView(self) -> object:
        return self.content_view

    def center(self) -> None:
        pass

    def makeKeyAndOrderFront_(self, _sender: object | None) -> None:
        pass

    def orderOut_(self, _sender: object | None) -> None:
        pass

    def close(self) -> None:
        pass

    def isKeyWindow(self) -> bool:
        return True


class _FakeContentView:
    def addSubview_(self, _view: object) -> None:
        pass


class _FakeLabel:
    def setFrame_(self, _frame: object) -> None:
        pass

    def setAlignment_(self, _alignment: int) -> None:
        pass

    def setTextColor_(self, _color: object) -> None:
        pass

    def setFont_(self, _font: object) -> None:
        pass


class _FakeAppKit:
    NSApplicationActivationPolicyRegular = 0
    NSWindowStyleMaskTitled = 1
    NSWindowStyleMaskClosable = 2
    NSBackingStoreBuffered = 2
    NSTextAlignmentCenter = 1
    NSEventTypeApplicationDefined = 15

    def __init__(self) -> None:
        self.application = _FakeApplication()
        self.event = object()
        self.NSApplication = _StaticFactory(self.application)
        self.NSWindow = _AllocFactory(_FakeWindow)
        self.NSColor = _FakeColor
        self.NSTextField = _FakeTextField
        self.NSFont = _FakeFont
        self.NSEvent = _FakeEvent(self.event)

    @staticmethod
    def NSMakeRect(
        x: float, y: float, width: float, height: float
    ) -> tuple[float, ...]:
        return x, y, width, height


class _StaticFactory:
    def __init__(self, value: object) -> None:
        self._value = value

    def sharedApplication(self) -> object:
        return self._value


class _AllocFactory:
    def __init__(self, factory: object) -> None:
        self._factory = factory

    def alloc(self) -> "_AllocFactory":
        return self

    def initWithContentRect_styleMask_backing_defer_(
        self,
        _frame: object,
        _style: int,
        _backing: int,
        _defer: bool,
    ) -> object:
        assert callable(self._factory)
        return self._factory()


class _FakeColor:
    @staticmethod
    def colorWithSRGBRed_green_blue_alpha_(
        _red: float,
        _green: float,
        _blue: float,
        _alpha: float,
    ) -> object:
        return object()

    @staticmethod
    def whiteColor() -> object:
        return object()


class _FakeTextField:
    @staticmethod
    def labelWithString_(_label: str) -> _FakeLabel:
        return _FakeLabel()


class _FakeFont:
    @staticmethod
    def boldSystemFontOfSize_(_size: float) -> object:
        return object()


class _FakeEvent:
    def __init__(self, event: object) -> None:
        self._event = event

    def __getattr__(self, name: str) -> object:
        if name.endswith("context_subtype_data1_data2_"):
            return self._make_event
        raise AttributeError(name)

    def _make_event(
        self,
        _event_type: int,
        _location: tuple[float, float],
        _modifiers: int,
        _timestamp: float,
        _window_number: int,
        _context: object | None,
        _subtype: int,
        _data1: int,
        _data2: int,
    ) -> object:
        return self._event


class _FakeFoundation:
    pass
