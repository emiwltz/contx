"""Signed native host bundles bind the complete private runtime inventory."""

from __future__ import annotations

import os
import plistlib
from collections.abc import Sequence
from pathlib import Path

import pytest

from contx.macos_app.bundle import MacOSAppBuildError, build_contx_app_bundle
from contx.macos_app.native_source import CONTX_APP_SWIFT_SOURCE, validate_native_source
from contx.macos_app.runtime_release import RuntimeReleaseError
from tests.unit.test_runtime_release import make_release

USAGE = "Disabled synthetic build without permission request."


class _FakeToolchain:
    def __init__(self, *, fail_compile: bool = False) -> None:
        self.commands: list[tuple[str, ...]] = []
        self.fail_compile = fail_compile

    def __call__(self, command: Sequence[str]) -> None:
        self.commands.append(tuple(command))
        if command[:2] != ("xcrun", "swiftc"):
            return
        if self.fail_compile:
            raise MacOSAppBuildError("synthetic native compile failure")
        Path(command[command.index("-o") + 1]).write_bytes(b"synthetic Mach-O")


def test_build_binds_complete_runtime_to_native_signature(tmp_path, monkeypatch):
    os.chmod(tmp_path, 0o700)
    manifest, path = make_release(tmp_path, monkeypatch)
    toolchain = _FakeToolchain()
    app = build_contx_app_bundle(
        tmp_path / "CONTX.app",
        runtime_manifest=path,
        signing_identity="Apple Development",
        screen_recording_usage_description=USAGE,
        run_command=toolchain,
    )
    info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
    sealed = plistlib.loads(
        (app / "Contents/Resources/RuntimeContract.plist").read_bytes()
    )
    assert info["CFBundleIdentifier"] == "io.contx.desktop"
    assert info["LSUIElement"] is False
    assert sealed == manifest.model_dump()
    assert toolchain.commands[1][:3] == ("/usr/bin/codesign", "--force", "--sign")
    assert toolchain.commands[2][:4] == (
        "/usr/bin/codesign",
        "--verify",
        "--deep",
        "--strict",
    )
    assert list(tmp_path.glob(".*-build-*")) == []


def test_refuses_changed_runtime_before_compiling(tmp_path, monkeypatch):
    manifest, path = make_release(tmp_path, monkeypatch)
    target = Path(manifest.root) / "module.py"
    target.chmod(0o600)
    target.write_text("changed")
    target.chmod(0o400)
    toolchain = _FakeToolchain()
    with pytest.raises(RuntimeReleaseError):
        build_contx_app_bundle(
            tmp_path / "CONTX.app",
            runtime_manifest=path,
            signing_identity="Apple Development",
            screen_recording_usage_description=USAGE,
            run_command=toolchain,
        )
    assert toolchain.commands == []
    assert not (tmp_path / "CONTX.app").exists()


def test_compile_failure_preserves_runtime_and_leaves_no_app(tmp_path, monkeypatch):
    manifest, path = make_release(tmp_path, monkeypatch)
    with pytest.raises(MacOSAppBuildError, match="synthetic"):
        build_contx_app_bundle(
            tmp_path / "CONTX.app",
            runtime_manifest=path,
            signing_identity="Apple Development",
            screen_recording_usage_description=USAGE,
            run_command=_FakeToolchain(fail_compile=True),
        )
    assert Path(manifest.root).is_dir()
    assert path.is_file()
    assert not (tmp_path / "CONTX.app").exists()
    assert list(tmp_path.glob(".*-build-*")) == []


@pytest.mark.parametrize("output", [Path("CONTX.app"), Path("/private/tmp/Other.app")])
def test_rejects_invalid_destination(output):
    with pytest.raises(MacOSAppBuildError):
        build_contx_app_bundle(
            output,
            runtime_manifest=Path("/missing"),
            signing_identity="test",
            screen_recording_usage_description=USAGE,
        )


def test_native_source_scope_and_close_contract():
    validate_native_source()
    assert "NSStatusItem" not in CONTX_APP_SWIFT_SOURCE
    assert "activationPolicy() == .regular" in CONTX_APP_SWIFT_SOURCE
    close = CONTX_APP_SWIFT_SOURCE.split("func windowShouldClose", 1)[1]
    close = close.split("func applicationShouldHandleReopen", 1)[0]
    assert "NSApplication.shared.terminate(nil)" in close
    assert "worker.sync {\n            stopCollector()" in CONTX_APP_SWIFT_SOURCE
    assert '"-I", "-B", "-m", "contx.daemon.entrypoint"' in CONTX_APP_SWIFT_SOURCE
    assert "runtimeVerifier.verify" in CONTX_APP_SWIFT_SOURCE
    assert "integrityFailed = true" in CONTX_APP_SWIFT_SOURCE
