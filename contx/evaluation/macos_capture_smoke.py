"""Host-dependent focused-window capture smoke with synthetic AppKit content."""

from __future__ import annotations

import hashlib
import os
import struct
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path
from threading import Event, Thread
from types import ModuleType
from typing import Any

from contx.collection import ActivitySample
from contx.collectors.macos import (
    CoreGraphicsFocusedWindowProbe,
    FocusedWindowTitleProbe,
    ScreenCaptureKitScreenshotSource,
    WorkspaceApplicationProbe,
)
from contx.errors import (
    CollectorUnavailableError,
    ScreenshotCaptureSkipped,
)
from contx.models import ActivityState

PRIMARY_TITLE = "CONTX Synthetic Focused Window"
SECONDARY_TITLE = "CONTX Synthetic Race Window"
CAPTURE_TIMEOUT_SECONDS = 5.0
WORKER_TIMEOUT_SECONDS = 7.0
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


@dataclass(frozen=True, slots=True)
class FocusedWindowSmokeResult:
    """Content-free evidence from one bounded live native smoke."""

    output_path: Path
    width: int
    height: int
    content_hash: str
    focus_race_reason: str


class _SyntheticWindowHost:
    """Own two harmless windows while servicing the main AppKit run loop."""

    def __init__(self, appkit: ModuleType, foundation: ModuleType) -> None:
        self._appkit = appkit
        self._foundation = foundation
        self._application: Any | None = None
        self._primary: Any | None = None
        self._secondary: Any | None = None

    def start(self) -> None:
        if self._application is not None:
            raise RuntimeError("synthetic window host is already started")
        try:
            application = self._appkit.NSApplication.sharedApplication()
            application.setActivationPolicy_(
                self._appkit.NSApplicationActivationPolicyRegular
            )
            self._application = application
            primary = self._create_window(
                title=PRIMARY_TITLE,
                red=0.12,
                green=0.28,
                blue=0.58,
            )
            self._primary = primary
            secondary = self._create_window(
                title=SECONDARY_TITLE,
                red=0.58,
                green=0.18,
                blue=0.22,
            )
            self._secondary = secondary
            secondary.orderOut_(None)
        except Exception as error:
            self.close()
            raise CollectorUnavailableError(
                "Cannot create the synthetic AppKit smoke windows"
            ) from error
        self.focus_primary()

    def focus_primary(self) -> None:
        self._focus(self._required_primary())

    def focus_secondary(self) -> None:
        self._focus(self._required_secondary())

    def pump(self, seconds: float) -> None:
        if seconds <= 0:
            return
        run_loop = self._foundation.NSRunLoop.currentRunLoop()
        deadline = time.monotonic() + seconds
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            until = self._foundation.NSDate.dateWithTimeIntervalSinceNow_(
                min(0.02, remaining)
            )
            run_loop.runMode_beforeDate_(
                self._foundation.NSDefaultRunLoopMode,
                until,
            )

    def close(self) -> None:
        for window in (self._secondary, self._primary):
            if window is None:
                continue
            try:
                window.orderOut_(None)
                window.close()
            except Exception:
                pass
        self._secondary = None
        self._primary = None
        self._application = None

    def _create_window(
        self,
        *,
        title: str,
        red: float,
        green: float,
        blue: float,
    ) -> Any:
        frame = self._appkit.NSMakeRect(0.0, 0.0, 640.0, 400.0)
        style = (
            self._appkit.NSWindowStyleMaskTitled
            | self._appkit.NSWindowStyleMaskClosable
        )
        window = (
            self._appkit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
                frame,
                style,
                self._appkit.NSBackingStoreBuffered,
                False,
            )
        )
        if window is None:
            raise RuntimeError("AppKit returned no synthetic window")
        window.setReleasedWhenClosed_(False)
        window.setTitle_(title)
        window.setBackgroundColor_(
            self._appkit.NSColor.colorWithSRGBRed_green_blue_alpha_(
                red,
                green,
                blue,
                1.0,
            )
        )
        label = self._appkit.NSTextField.labelWithString_(
            "Synthetic CONTX capture fixture\nNo user content"
        )
        label.setFrame_(self._appkit.NSMakeRect(40.0, 150.0, 560.0, 100.0))
        label.setAlignment_(self._appkit.NSTextAlignmentCenter)
        label.setTextColor_(self._appkit.NSColor.whiteColor())
        label.setFont_(self._appkit.NSFont.boldSystemFontOfSize_(24.0))
        content_view = window.contentView()
        if content_view is None:
            raise RuntimeError("AppKit returned no synthetic content view")
        content_view.addSubview_(label)
        window.center()
        return window

    def _focus(self, window: Any) -> None:
        application = self._required_application()
        window.makeKeyAndOrderFront_(None)
        application.activateIgnoringOtherApps_(True)
        self.pump(0.35)

    def _required_application(self) -> Any:
        if self._application is None:
            raise RuntimeError("synthetic window host is not started")
        return self._application

    def _required_primary(self) -> Any:
        if self._primary is None:
            raise RuntimeError("synthetic primary window is unavailable")
        return self._primary

    def _required_secondary(self) -> Any:
        if self._secondary is None:
            raise RuntimeError("synthetic secondary window is unavailable")
        return self._secondary


def run_focused_window_smoke(output_path: Path) -> FocusedWindowSmokeResult:
    """Capture one synthetic window after proving an intra-process focus race."""
    if sys.platform != "darwin":
        raise CollectorUnavailableError(
            "The focused-window smoke test is available only on macOS"
        )
    target = validate_private_output_path(output_path)
    appkit, foundation = _load_native_modules()
    host = _SyntheticWindowHost(appkit, foundation)
    application = WorkspaceApplicationProbe()
    window = CoreGraphicsFocusedWindowProbe()
    title = FocusedWindowTitleProbe()
    source = ScreenCaptureKitScreenshotSource(
        application=application,
        window_title=title,
        timeout_seconds=CAPTURE_TIMEOUT_SECONDS,
    )
    try:
        host.start()
        primary_sample = _sample_expected_window(
            expected_title=PRIMARY_TITLE,
            application=application,
            window=window,
            title=title,
        )
        host.focus_secondary()
        focus_race_reason = _expect_focus_race_skip(
            source,
            primary_sample,
            host,
        )
        host.focus_primary()
        capture_sample = _sample_expected_window(
            expected_title=PRIMARY_TITLE,
            application=application,
            window=window,
            title=title,
        )
        payload = _capture_with_run_loop(source, capture_sample, host)
        width, height = png_dimensions(payload)
        target = write_private_png(target, payload)
    finally:
        host.close()
    return FocusedWindowSmokeResult(
        output_path=target,
        width=width,
        height=height,
        content_hash=hashlib.sha256(payload).hexdigest(),
        focus_race_reason=focus_race_reason,
    )


def png_dimensions(payload: bytes) -> tuple[int, int]:
    """Read bounded PNG IHDR dimensions without decoding private pixels."""
    if len(payload) < 24 or not payload.startswith(PNG_SIGNATURE):
        raise CollectorUnavailableError(
            "The smoke capture did not return a complete PNG header"
        )
    if payload[12:16] != b"IHDR":
        raise CollectorUnavailableError("The smoke capture PNG has no IHDR chunk")
    width, height = struct.unpack(">II", payload[16:24])
    if width <= 0 or height <= 0:
        raise CollectorUnavailableError("The smoke capture PNG has invalid dimensions")
    return width, height


def write_private_png(output_path: Path, payload: bytes) -> Path:
    """Write one exclusive 0600 smoke artifact to an explicit existing parent."""
    target = validate_private_output_path(output_path)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(target, flags, 0o600)
        with os.fdopen(descriptor, "wb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
    except FileExistsError as error:
        raise ValueError("smoke output path already exists") from error
    except OSError as error:
        raise CollectorUnavailableError(
            "Cannot write the private smoke capture artifact"
        ) from error
    return target


def validate_private_output_path(output_path: Path) -> Path:
    """Resolve a new explicit PNG target without following a target symlink."""
    if not output_path.is_absolute():
        raise ValueError("smoke output path must be absolute")
    if output_path.suffix.lower() != ".png":
        raise ValueError("smoke output path must end in .png")
    try:
        parent = output_path.parent.resolve(strict=True)
    except OSError as error:
        raise CollectorUnavailableError(
            "The smoke output parent is unavailable"
        ) from error
    if not parent.is_dir():
        raise ValueError("smoke output parent must be a directory")
    target = parent / output_path.name
    if target.exists() or target.is_symlink():
        raise ValueError("smoke output path already exists")
    return target


def _sample_expected_window(
    *,
    expected_title: str,
    application: WorkspaceApplicationProbe,
    window: CoreGraphicsFocusedWindowProbe,
    title: FocusedWindowTitleProbe,
) -> ActivitySample:
    metadata = application.read()
    window_id = window.read(process_id=metadata.process_id)
    if window_id is None:
        raise CollectorUnavailableError(
            "The synthetic AppKit window has no capturable window ID"
        )
    observed_title = title.read()
    if observed_title != expected_title:
        raise CollectorUnavailableError(
            "The synthetic AppKit window did not become the focused window"
        )
    return ActivitySample(
        observed_at=datetime.now(UTC),
        activity_state=ActivityState.ACTIVE,
        app_name=metadata.app_name,
        app_bundle_id=metadata.app_bundle_id,
        window_title=observed_title,
        process_id=metadata.process_id,
        window_id=window_id,
    )


def _expect_focus_race_skip(
    source: ScreenCaptureKitScreenshotSource,
    primary_sample: ActivitySample,
    host: _SyntheticWindowHost,
) -> str:
    try:
        _capture_with_run_loop(source, primary_sample, host)
    except ScreenshotCaptureSkipped as skipped:
        if skipped.reason_code not in {
            "focused_window_changed",
            "focused_window_title_changed",
        }:
            raise CollectorUnavailableError(
                "The synthetic focus race returned an unexpected result"
            ) from skipped
        return skipped.reason_code
    raise CollectorUnavailableError(
        "The synthetic focus race captured a window that was not authorized"
    )


def _capture_with_run_loop(
    source: ScreenCaptureKitScreenshotSource,
    sample: ActivitySample,
    host: _SyntheticWindowHost,
) -> bytes:
    finished = Event()
    payload: list[bytes] = []
    failures: list[Exception] = []

    def capture() -> None:
        try:
            payload.append(source.capture_png(sample))
        except Exception as error:
            failures.append(error)
        finally:
            finished.set()

    worker = Thread(target=capture, name="contx-smoke-capture", daemon=True)
    worker.start()
    deadline = time.monotonic() + WORKER_TIMEOUT_SECONDS
    while not finished.is_set() and time.monotonic() < deadline:
        host.pump(0.02)
    if not finished.is_set():
        raise CollectorUnavailableError(
            "The synthetic ScreenCaptureKit worker did not finish"
        )
    worker.join(timeout=0.1)
    if failures:
        raise failures[0]
    if len(payload) != 1:
        raise CollectorUnavailableError(
            "The synthetic ScreenCaptureKit worker returned no image"
        )
    return payload[0]


def _load_native_modules() -> tuple[ModuleType, ModuleType]:
    try:
        appkit = import_module("AppKit")
        foundation = import_module("Foundation")
    except ImportError as error:
        raise CollectorUnavailableError(
            "The macOS Cocoa bridge is unavailable for the smoke test"
        ) from error
    return appkit, foundation
