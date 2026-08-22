from __future__ import annotations

import base64
import hashlib
import os
import plistlib
import stat
from collections.abc import Sequence
from pathlib import Path

import pytest

from scripts.build_macos_identity_probe import MacOSProbeBuildError
from scripts.build_macos_permission_probe import (
    PERMISSION_CHILD_NAME,
    PERMISSION_CHILD_SOURCE,
    PERMISSION_SWIFT_SOURCE_TEMPLATE,
    REQUEST_CONFIRMATION,
    VERIFY_CONFIRMATION,
    build_permission_attribution_probe_bundle,
)

USAGE_DESCRIPTION = (
    "CONTX verifies Screen Recording attribution without capturing any pixels."
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
            raise MacOSProbeBuildError("synthetic permission compile failure")
        output = Path(captured[captured.index("-o") + 1])
        output.write_bytes(b"synthetic permission Mach-O")


def _python_executable(tmp_path: Path) -> Path:
    executable = tmp_path / "python"
    executable.write_bytes(b"synthetic python")
    os.chmod(executable, 0o700)
    return executable


def test_builds_gated_permission_probe_with_sealed_child(tmp_path: Path) -> None:
    os.chmod(tmp_path, 0o700)
    python_executable = _python_executable(tmp_path)
    output = tmp_path / "CONTX Permission Probe.app"
    toolchain = _FakeToolchain()

    result = build_permission_attribution_probe_bundle(
        output,
        python_executable=python_executable,
        usage_description=USAGE_DESCRIPTION,
        run_command=toolchain,
    )

    assert result == output
    executable = output / "Contents/MacOS/CONTXIdentityProbe"
    child = output / f"Contents/Resources/{PERMISSION_CHILD_NAME}"
    info_plist = output / "Contents/Info.plist"
    assert executable.read_bytes() == b"synthetic permission Mach-O"
    assert child.read_text(encoding="utf-8") == PERMISSION_CHILD_SOURCE
    assert stat.S_IMODE(child.stat().st_mode) == 0o600
    with info_plist.open("rb") as file:
        metadata = plistlib.load(file)
    assert metadata["NSScreenCaptureUsageDescription"] == USAGE_DESCRIPTION
    assert toolchain.swift_source is not None
    encoded_path = base64.b64encode(os.fsencode(str(python_executable))).decode("ascii")
    assert encoded_path in toolchain.swift_source
    expected_digest = hashlib.sha256(python_executable.read_bytes()).hexdigest()
    assert expected_digest in toolchain.swift_source
    assert str(python_executable) not in toolchain.swift_source
    assert REQUEST_CONFIRMATION in toolchain.swift_source
    assert VERIFY_CONFIRMATION in toolchain.swift_source
    assert toolchain.commands[0][3:9] == (
        "-framework",
        "AppKit",
        "-framework",
        "CoreGraphics",
        "-framework",
        "Foundation",
    )
    assert list(tmp_path.glob(".*-build-*")) == []


@pytest.mark.parametrize(
    "usage_description",
    [
        "too short",
        " " + USAGE_DESCRIPTION,
        USAGE_DESCRIPTION + "\nsecond line",
        "x" * 241,
    ],
)
def test_rejects_invalid_usage_description(
    tmp_path: Path,
    usage_description: str,
) -> None:
    os.chmod(tmp_path, 0o700)

    with pytest.raises(MacOSProbeBuildError, match="usage description"):
        build_permission_attribution_probe_bundle(
            tmp_path / "Probe.app",
            python_executable=_python_executable(tmp_path),
            usage_description=usage_description,
            run_command=_FakeToolchain(),
        )


def test_rejects_relative_python_executable(tmp_path: Path) -> None:
    os.chmod(tmp_path, 0o700)

    with pytest.raises(MacOSProbeBuildError, match="must be absolute"):
        build_permission_attribution_probe_bundle(
            tmp_path / "Probe.app",
            python_executable=Path(".venv/bin/python"),
            usage_description=USAGE_DESCRIPTION,
            run_command=_FakeToolchain(),
        )


def test_rejects_non_executable_python_file(tmp_path: Path) -> None:
    os.chmod(tmp_path, 0o700)
    python_executable = tmp_path / "python"
    python_executable.write_bytes(b"not executable")
    os.chmod(python_executable, 0o600)

    with pytest.raises(MacOSProbeBuildError, match="executable file"):
        build_permission_attribution_probe_bundle(
            tmp_path / "Probe.app",
            python_executable=python_executable,
            usage_description=USAGE_DESCRIPTION,
            run_command=_FakeToolchain(),
        )


def test_compile_failure_leaves_no_permission_bundle(tmp_path: Path) -> None:
    os.chmod(tmp_path, 0o700)
    python_executable = _python_executable(tmp_path)
    output = tmp_path / "Probe.app"

    with pytest.raises(
        MacOSProbeBuildError,
        match="synthetic permission compile failure",
    ):
        build_permission_attribution_probe_bundle(
            output,
            python_executable=python_executable,
            usage_description=USAGE_DESCRIPTION,
            run_command=_FakeToolchain(fail_compile=True),
        )

    assert not output.exists()
    assert list(tmp_path.glob(".*-build-*")) == []


def test_sources_are_gated_and_non_capturing() -> None:
    assert PERMISSION_SWIFT_SOURCE_TEMPLATE.count("CGRequestScreenCaptureAccess()") == 1
    assert REQUEST_CONFIRMATION in PERMISSION_SWIFT_SOURCE_TEMPLATE
    assert VERIFY_CONFIRMATION in PERMISSION_SWIFT_SOURCE_TEMPLATE
    assert "case identity\n    case request\n    case verify" in (
        PERMISSION_SWIFT_SOURCE_TEMPLATE
    )
    assert "pixelsRead: false" in PERMISSION_SWIFT_SOURCE_TEMPLATE
    assert "collectionStarted: false" in PERMISSION_SWIFT_SOURCE_TEMPLATE
    assert "sha256Hex(executableData)" in PERMISSION_SWIFT_SOURCE_TEMPLATE
    assert "!= evidenceFile.standardizedFileURL.path" in (
        PERMISSION_SWIFT_SOURCE_TEMPLATE
    )
    assert "SCScreenshotManager" not in PERMISSION_SWIFT_SOURCE_TEMPLATE
    assert PERMISSION_CHILD_SOURCE.count("CGPreflightScreenCaptureAccess()") == 1
    assert "CGRequestScreenCaptureAccess" not in PERMISSION_CHILD_SOURCE
    assert '"pixels_read": False' in PERMISSION_CHILD_SOURCE
    assert '"collection_started": False' in PERMISSION_CHILD_SOURCE
