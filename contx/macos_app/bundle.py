"""Atomic, signed build path for the native CONTX macOS host."""

from __future__ import annotations

import os
import plistlib
import shutil
import stat
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path

from contx.macos_app.native_source import (
    CONTX_APP_SWIFT_SOURCE,
    validate_native_source,
)
from contx.macos_app.runtime_release import load_manifest

BUNDLE_IDENTIFIER = "io.contx.desktop"
BUNDLE_NAME = "CONTX"
EXECUTABLE_NAME = "CONTX"
RUNTIME_CONTRACT_NAME = "RuntimeContract.plist"
RUNTIME_CONTRACT_SCHEMA_VERSION = 2
PRIVATE_DIRECTORY_MODE = 0o700
PRIVATE_FILE_MODE = 0o600
PRIVATE_EXECUTABLE_MODE = 0o700


class MacOSAppBuildError(RuntimeError):
    """Report a bounded native-host build failure."""


CommandRunner = Callable[[Sequence[str]], None]


def build_contx_app_bundle(
    output: Path,
    *,
    runtime_manifest: Path,
    signing_identity: str,
    screen_recording_usage_description: str,
    platform: str = sys.platform,
    run_command: CommandRunner | None = None,
) -> Path:
    """Build, sign, and verify one new bundle without launching or installing it."""
    if platform != "darwin":
        raise MacOSAppBuildError("CONTX.app can be built only on macOS")
    validate_native_source()
    destination = _validate_destination(output)
    manifest = load_manifest(runtime_manifest)
    identity = _validate_single_line(
        signing_identity,
        name="signing identity",
        minimum=1,
        maximum=255,
    )
    usage_description = _validate_single_line(
        screen_recording_usage_description,
        name="Screen Recording usage description",
        minimum=40,
        maximum=240,
    )
    runner = run_command or _run_command
    temporary_root = Path(
        tempfile.mkdtemp(
            prefix=f".{destination.stem}-build-",
            dir=destination.parent,
        )
    )
    os.chmod(temporary_root, PRIVATE_DIRECTORY_MODE)
    staged_app = temporary_root / destination.name
    contents = staged_app / "Contents"
    executable_directory = contents / "MacOS"
    resources_directory = contents / "Resources"
    executable_directory.mkdir(parents=True, mode=PRIVATE_DIRECTORY_MODE)
    resources_directory.mkdir(mode=PRIVATE_DIRECTORY_MODE)
    for directory in (
        staged_app,
        contents,
        executable_directory,
        resources_directory,
    ):
        os.chmod(directory, PRIVATE_DIRECTORY_MODE)
    source_file = temporary_root / "CONTXApp.swift"
    executable = executable_directory / EXECUTABLE_NAME
    info_plist = contents / "Info.plist"
    runtime_contract = resources_directory / RUNTIME_CONTRACT_NAME
    try:
        source_file.write_text(CONTX_APP_SWIFT_SOURCE, encoding="utf-8")
        os.chmod(source_file, PRIVATE_FILE_MODE)
        compile_command = ["xcrun", "swiftc", str(source_file)]
        compile_command.extend(
            (
                "-framework",
                "AppKit",
                "-framework",
                "CryptoKit",
                "-framework",
                "Foundation",
                "-framework",
                "Security",
                "-framework",
                "ApplicationServices",
                "-framework",
                "CoreGraphics",
                "-o",
                str(executable),
            )
        )
        runner(tuple(compile_command))
        if not executable.is_file() or executable.is_symlink():
            raise MacOSAppBuildError("Swift did not create the CONTX app executable")
        os.chmod(executable, PRIVATE_EXECUTABLE_MODE)
        with runtime_contract.open("wb") as stream:
            plistlib.dump(manifest.model_dump(), stream, fmt=plistlib.FMT_BINARY)
        os.chmod(runtime_contract, PRIVATE_FILE_MODE)
        _write_info_plist(
            info_plist,
            screen_recording_usage_description=usage_description,
        )
        runner(
            (
                "/usr/bin/codesign",
                "--force",
                "--sign",
                identity,
                "--timestamp=none",
                "--options",
                "runtime",
                "--identifier",
                BUNDLE_IDENTIFIER,
                str(staged_app),
            )
        )
        runner(
            (
                "/usr/bin/codesign",
                "--verify",
                "--deep",
                "--strict",
                "--verbose=2",
                str(staged_app),
            )
        )
        os.replace(staged_app, destination)
    except Exception:
        if destination.exists():
            raise MacOSAppBuildError(
                "CONTX app build unexpectedly touched the destination"
            ) from None
        raise
    finally:
        shutil.rmtree(temporary_root, ignore_errors=True)
    return destination


def _validate_destination(output: Path) -> Path:
    if not output.is_absolute():
        raise MacOSAppBuildError("CONTX app output must be absolute")
    if output.name != "CONTX.app":
        raise MacOSAppBuildError("CONTX app output must end in CONTX.app")
    if output.exists() or output.is_symlink():
        raise MacOSAppBuildError("CONTX app output must not exist")
    try:
        parent = output.parent.resolve(strict=True)
    except OSError as error:
        raise MacOSAppBuildError(
            "CONTX app parent directory must already exist"
        ) from error
    if parent != output.parent or not parent.is_dir():
        raise MacOSAppBuildError("CONTX app parent must be a canonical directory")
    if stat.S_IMODE(parent.stat().st_mode) & 0o077:
        raise MacOSAppBuildError("CONTX app parent directory permissions are too broad")
    return parent / output.name


def _validate_single_line(
    value: str,
    *,
    name: str,
    minimum: int,
    maximum: int,
) -> str:
    normalized = value.strip()
    if (
        normalized != value
        or not minimum <= len(normalized) <= maximum
        or any(character in value for character in "\r\n\0")
    ):
        raise MacOSAppBuildError(
            f"CONTX {name} must contain {minimum} to {maximum} safe characters"
        )
    return normalized


def _write_info_plist(
    path: Path,
    *,
    screen_recording_usage_description: str,
) -> None:
    payload: dict[str, object] = {
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
        "NSScreenCaptureUsageDescription": screen_recording_usage_description,
    }
    with path.open("wb") as file:
        plistlib.dump(payload, file, fmt=plistlib.FMT_BINARY, sort_keys=True)
    os.chmod(path, PRIVATE_FILE_MODE)


def _run_command(command: Sequence[str]) -> None:
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise MacOSAppBuildError(
            f"CONTX app tool failed to run: {command[0]}"
        ) from error
    if completed.returncode == 0:
        return
    detail = (completed.stderr or completed.stdout).strip()
    if len(detail) > 1000:
        detail = detail[:1000] + "..."
    suffix = "" if not detail else f": {detail}"
    raise MacOSAppBuildError(f"CONTX app tool failed: {command[0]}{suffix}")
