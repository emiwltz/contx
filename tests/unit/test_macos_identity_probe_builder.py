from __future__ import annotations

import os
import plistlib
import stat
from collections.abc import Sequence
from pathlib import Path

import pytest

from scripts.build_macos_identity_probe import (
    BUNDLE_IDENTIFIER,
    BUNDLE_NAME,
    EXECUTABLE_NAME,
    IDENTITY_PROBE_SWIFT_SOURCE,
    IdentityProbeBuildError,
    build_identity_probe_bundle,
)


class _FakeToolchain:
    def __init__(self, *, fail_compile: bool = False) -> None:
        self.commands: list[tuple[str, ...]] = []
        self.fail_compile = fail_compile

    def __call__(self, command: Sequence[str]) -> None:
        captured = tuple(command)
        self.commands.append(captured)
        if captured[:2] != ("xcrun", "swiftc"):
            return
        if self.fail_compile:
            raise IdentityProbeBuildError("synthetic compile failure")
        output = Path(captured[captured.index("-o") + 1])
        output.write_bytes(b"synthetic Mach-O")


def test_builds_private_signed_identity_bundle_atomically(tmp_path: Path) -> None:
    os.chmod(tmp_path, 0o700)
    output = tmp_path / "CONTX Identity Probe.app"
    toolchain = _FakeToolchain()

    result = build_identity_probe_bundle(output, run_command=toolchain)

    assert result == output
    assert output.is_dir()
    info_plist = output / "Contents/Info.plist"
    executable = output / f"Contents/MacOS/{EXECUTABLE_NAME}"
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
        "LSUIElement": True,
        "NSHighResolutionCapable": True,
        "NSPrincipalClass": "NSApplication",
    }
    assert executable.read_bytes() == b"synthetic Mach-O"
    assert stat.S_IMODE(info_plist.stat().st_mode) == 0o600
    assert stat.S_IMODE(executable.stat().st_mode) == 0o700
    assert toolchain.commands[1][:7] == (
        "/usr/bin/codesign",
        "--force",
        "--sign",
        "-",
        "--timestamp=none",
        "--identifier",
        BUNDLE_IDENTIFIER,
    )
    assert toolchain.commands[2][:4] == (
        "/usr/bin/codesign",
        "--verify",
        "--deep",
        "--strict",
    )
    assert list(tmp_path.glob(".*-build-*")) == []


@pytest.mark.parametrize(
    ("output", "message"),
    [
        (Path("relative.app"), "must be absolute"),
        (Path("/private/tmp/not-an-app"), "must end in .app"),
    ],
)
def test_rejects_invalid_destination(output: Path, message: str) -> None:
    with pytest.raises(IdentityProbeBuildError, match=message):
        build_identity_probe_bundle(output, run_command=_FakeToolchain())


def test_rejects_existing_destination(tmp_path: Path) -> None:
    os.chmod(tmp_path, 0o700)
    output = tmp_path / "Existing.app"
    output.mkdir()

    with pytest.raises(IdentityProbeBuildError, match="must not exist"):
        build_identity_probe_bundle(output, run_command=_FakeToolchain())


def test_rejects_broad_parent_permissions(tmp_path: Path) -> None:
    os.chmod(tmp_path, 0o755)

    with pytest.raises(IdentityProbeBuildError, match="permissions are too broad"):
        build_identity_probe_bundle(
            tmp_path / "Probe.app",
            run_command=_FakeToolchain(),
        )


def test_compile_failure_leaves_no_bundle(tmp_path: Path) -> None:
    os.chmod(tmp_path, 0o700)
    output = tmp_path / "Probe.app"

    with pytest.raises(IdentityProbeBuildError, match="synthetic compile failure"):
        build_identity_probe_bundle(
            output,
            run_command=_FakeToolchain(fail_compile=True),
        )

    assert not output.exists()
    assert list(tmp_path.iterdir()) == []


def test_non_macos_build_is_rejected_before_touching_output(tmp_path: Path) -> None:
    os.chmod(tmp_path, 0o700)
    output = tmp_path / "Probe.app"

    with pytest.raises(IdentityProbeBuildError, match="only on macOS"):
        build_identity_probe_bundle(
            output,
            platform="linux",
            run_command=_FakeToolchain(),
        )

    assert not output.exists()


def test_probe_source_is_identity_only_and_bounded() -> None:
    assert "import AppKit" in IDENTITY_PROBE_SWIFT_SOURCE
    assert "import Foundation" in IDENTITY_PROBE_SWIFT_SOURCE
    assert 'probeKind: "identity_only"' in IDENTITY_PROBE_SWIFT_SOURCE
    assert "permissionsRequested: false" in IDENTITY_PROBE_SWIFT_SOURCE
    assert "collectionStarted: false" in IDENTITY_PROBE_SWIFT_SOURCE
    assert "(1.0...30.0).contains(lifetimeSeconds)" in IDENTITY_PROBE_SWIFT_SOURCE
    assert "private let delegate = ProbeDelegate(" in IDENTITY_PROBE_SWIFT_SOURCE
    assert "ScreenCaptureKit" not in IDENTITY_PROBE_SWIFT_SOURCE
    assert "CGRequestScreenCaptureAccess" not in IDENTITY_PROBE_SWIFT_SOURCE
    assert "NSWorkspace" not in IDENTITY_PROBE_SWIFT_SOURCE
