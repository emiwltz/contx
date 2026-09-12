"""Signed native host bundle construction without launch or installation."""

from __future__ import annotations

import hashlib
import os
import plistlib
import stat
from collections.abc import Sequence
from pathlib import Path

import pytest

from contx.macos_app.bundle import (
    BUNDLE_IDENTIFIER,
    BUNDLE_NAME,
    EXECUTABLE_NAME,
    RUNTIME_CONTRACT_NAME,
    MacOSAppBuildError,
    build_contx_app_bundle,
)
from contx.macos_app.native_source import (
    CONTX_APP_SWIFT_SOURCE,
    validate_native_source,
)

USAGE_DESCRIPTION = (
    "CONTX captures only an authorized focused window after exclusion checks."
)


class _FakeToolchain:
    def __init__(self, *, fail_compile: bool = False) -> None:
        self.commands: list[tuple[str, ...]] = []
        self.swift_source: str | None = None
        self.fail_compile = fail_compile

    def __call__(self, command: Sequence[str]) -> None:
        captured = tuple(command)
        self.commands.append(captured)
        if captured[:2] != ("xcrun", "swiftc"):
            return
        self.swift_source = Path(captured[2]).read_text(encoding="utf-8")
        if self.fail_compile:
            raise MacOSAppBuildError("synthetic native compile failure")
        output = Path(captured[captured.index("-o") + 1])
        output.write_bytes(b"synthetic signed host Mach-O")


def test_builds_private_signed_native_host_atomically(tmp_path: Path) -> None:
    os.chmod(tmp_path, 0o700)
    collector = _executable(tmp_path / "contx-collector", b"collector")
    control = _executable(tmp_path / "contx-native-control", b"control")
    output = tmp_path / "CONTX.app"
    toolchain = _FakeToolchain()

    result = build_contx_app_bundle(
        output,
        collector_executable=collector,
        control_executable=control,
        signing_identity="Apple Development",
        screen_recording_usage_description=USAGE_DESCRIPTION,
        run_command=toolchain,
    )

    assert result == output
    executable = output / f"Contents/MacOS/{EXECUTABLE_NAME}"
    info_plist = output / "Contents/Info.plist"
    runtime_contract = output / f"Contents/Resources/{RUNTIME_CONTRACT_NAME}"
    assert executable.read_bytes() == b"synthetic signed host Mach-O"
    with info_plist.open("rb") as file:
        metadata = plistlib.load(file)
    assert metadata == {
        "CFBundleDevelopmentRegion": "en",
        "CFBundleDisplayName": BUNDLE_NAME,
        "CFBundleExecutable": EXECUTABLE_NAME,
        "CFBundleIdentifier": BUNDLE_IDENTIFIER,
        "CFBundleInfoDictionaryVersion": "6.0",
        "CFBundleName": BUNDLE_NAME,
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": "0.0.1",
        "CFBundleVersion": "1",
        "LSMinimumSystemVersion": "14.0",
        "LSUIElement": False,
        "NSHighResolutionCapable": True,
        "NSPrincipalClass": "NSApplication",
        "NSScreenCaptureUsageDescription": USAGE_DESCRIPTION,
    }
    with runtime_contract.open("rb") as file:
        contract = plistlib.load(file)
    assert contract == {
        "schemaVersion": 1,
        "collector": {
            "path": str(collector),
            "sha256": hashlib.sha256(b"collector").hexdigest(),
        },
        "control": {
            "path": str(control),
            "sha256": hashlib.sha256(b"control").hexdigest(),
        },
    }
    assert stat.S_IMODE(executable.stat().st_mode) == 0o700
    assert stat.S_IMODE(info_plist.stat().st_mode) == 0o600
    assert stat.S_IMODE(runtime_contract.stat().st_mode) == 0o600
    assert toolchain.swift_source == CONTX_APP_SWIFT_SOURCE
    assert toolchain.commands[1][0:8] == (
        "/usr/bin/codesign",
        "--force",
        "--sign",
        "Apple Development",
        "--timestamp=none",
        "--options",
        "runtime",
        "--identifier",
    )
    assert toolchain.commands[2][0:5] == (
        "/usr/bin/codesign",
        "--verify",
        "--deep",
        "--strict",
        "--verbose=2",
    )
    assert list(tmp_path.glob(".*-build-*")) == []


def test_build_rejects_unsafe_or_mutable_runtime_commands(tmp_path: Path) -> None:
    os.chmod(tmp_path, 0o700)
    executable = _executable(tmp_path / "collector", b"collector")
    link = tmp_path / "control"
    link.symlink_to(executable)

    with pytest.raises(MacOSAppBuildError, match="non-symlink"):
        build_contx_app_bundle(
            tmp_path / "CONTX.app",
            collector_executable=executable,
            control_executable=link,
            signing_identity="Apple Development",
            screen_recording_usage_description=USAGE_DESCRIPTION,
            run_command=_FakeToolchain(),
        )

    assert not (tmp_path / "CONTX.app").exists()


@pytest.mark.parametrize(
    ("output", "message"),
    [
        (Path("CONTX.app"), "must be absolute"),
        (Path("/private/tmp/Other.app"), "must end in CONTX.app"),
    ],
)
def test_build_rejects_invalid_destination(output: Path, message: str) -> None:
    with pytest.raises(MacOSAppBuildError, match=message):
        build_contx_app_bundle(
            output,
            collector_executable=Path("/private/tmp/missing-collector"),
            control_executable=Path("/private/tmp/missing-control"),
            signing_identity="Apple Development",
            screen_recording_usage_description=USAGE_DESCRIPTION,
            run_command=_FakeToolchain(),
        )


def test_compile_failure_leaves_no_bundle_or_staging_directory(
    tmp_path: Path,
) -> None:
    os.chmod(tmp_path, 0o700)
    collector = _executable(tmp_path / "collector", b"collector")
    control = _executable(tmp_path / "control", b"control")

    with pytest.raises(MacOSAppBuildError, match="synthetic native compile failure"):
        build_contx_app_bundle(
            tmp_path / "CONTX.app",
            collector_executable=collector,
            control_executable=control,
            signing_identity="Apple Development",
            screen_recording_usage_description=USAGE_DESCRIPTION,
            run_command=_FakeToolchain(fail_compile=True),
        )

    assert not (tmp_path / "CONTX.app").exists()
    assert list(tmp_path.glob(".*-build-*")) == []


def test_native_source_is_window_and_process_only() -> None:
    validate_native_source()

    assert "NSStatusItem" not in CONTX_APP_SWIFT_SOURCE
    assert "CONTX_STATUS_ITEM_TEXT_DIAGNOSTIC" not in CONTX_APP_SWIFT_SOURCE
    assert "activationPolicy() == .regular" in CONTX_APP_SWIFT_SOURCE
    assert "NSWindowDelegate" in CONTX_APP_SWIFT_SOURCE
    assert "window.makeKeyAndOrderFront(nil)" in CONTX_APP_SWIFT_SOURCE
    assert "func windowShouldClose" in CONTX_APP_SWIFT_SOURCE
    close_handler = CONTX_APP_SWIFT_SOURCE.split("func windowShouldClose", 1)[1]
    close_handler = close_handler.split("func applicationShouldHandleReopen", 1)[0]
    assert "NSApplication.shared.terminate(nil)" in close_handler
    assert "return false" in close_handler
    assert "worker.sync {\n            stopCollector()" in CONTX_APP_SWIFT_SOURCE
    assert 'NSButton(title: "Quit CONTX", target: NSApplication.shared' in (
        CONTX_APP_SWIFT_SOURCE
    )
    assert 'arguments: ["status"]' in CONTX_APP_SWIFT_SOURCE
    assert 'arguments: ["pause"]' in CONTX_APP_SWIFT_SOURCE
    assert 'arguments: ["resume"]' in CONTX_APP_SWIFT_SOURCE
    assert 'environment["CONTX_NATIVE_HOST"] = "1"' in CONTX_APP_SWIFT_SOURCE
    assert "SHA256.hash(data: data)" in CONTX_APP_SWIFT_SOURCE
    assert "isExecutableFile(atPath: command.path)" in CONTX_APP_SWIFT_SOURCE
    assert "maximumControlOutputBytes = 32 * 1024" in CONTX_APP_SWIFT_SOURCE
    assert "controlTimeoutSeconds: TimeInterval = 5.0" in CONTX_APP_SWIFT_SOURCE
    assert "CGRequestScreenCaptureAccess" not in CONTX_APP_SWIFT_SOURCE
    assert "ScreenCaptureKit" not in CONTX_APP_SWIFT_SOURCE
    assert "URLSession" not in CONTX_APP_SWIFT_SOURCE


def _executable(path: Path, content: bytes) -> Path:
    path.write_bytes(content)
    path.chmod(0o700)
    return path
