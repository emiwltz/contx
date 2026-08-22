"""Atomic, signed build path for the native CONTX macOS host."""

from __future__ import annotations

import hashlib
import os
import plistlib
import shutil
import stat
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from contx.macos_app.native_source import (
    CONTX_APP_SWIFT_SOURCE,
    validate_native_source,
)

BUNDLE_IDENTIFIER = "io.contx.desktop"
BUNDLE_NAME = "CONTX"
EXECUTABLE_NAME = "CONTX"
RUNTIME_CONTRACT_NAME = "RuntimeContract.plist"
RUNTIME_CONTRACT_SCHEMA_VERSION = 1
STATUS_ITEM_TEXT_DIAGNOSTIC_COMPILER_FLAG = "CONTX_STATUS_ITEM_TEXT_DIAGNOSTIC"
STATUS_ITEM_TEXT_DIAGNOSTIC_INFO_KEY = "CONTXStatusItemTextDiagnostic"
PRIVATE_DIRECTORY_MODE = 0o700
PRIVATE_FILE_MODE = 0o600
PRIVATE_EXECUTABLE_MODE = 0o700


class MacOSAppBuildError(RuntimeError):
    """Report a bounded native-host build failure."""


CommandRunner = Callable[[Sequence[str]], None]


@dataclass(frozen=True, slots=True)
class RuntimeCommandRecord:
    """One exact external development command pinned into the signed bundle."""

    path: Path
    sha256: str

    def as_plist(self) -> dict[str, str]:
        return {"path": str(self.path), "sha256": self.sha256}


def build_contx_app_bundle(
    output: Path,
    *,
    collector_executable: Path,
    control_executable: Path,
    signing_identity: str,
    screen_recording_usage_description: str,
    status_item_text_diagnostic: bool = False,
    platform: str = sys.platform,
    run_command: CommandRunner | None = None,
) -> Path:
    """Build, sign, and verify one new bundle without launching or installing it."""
    if platform != "darwin":
        raise MacOSAppBuildError("CONTX.app can be built only on macOS")
    validate_native_source()
    destination = _validate_destination(output)
    collector = _runtime_command(collector_executable, name="collector")
    control = _runtime_command(control_executable, name="control")
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
        if status_item_text_diagnostic:
            compile_command.extend(("-D", STATUS_ITEM_TEXT_DIAGNOSTIC_COMPILER_FLAG))
        compile_command.extend(
            (
                "-framework",
                "AppKit",
                "-framework",
                "CryptoKit",
                "-framework",
                "Foundation",
                "-o",
                str(executable),
            )
        )
        runner(tuple(compile_command))
        if not executable.is_file() or executable.is_symlink():
            raise MacOSAppBuildError("Swift did not create the CONTX app executable")
        os.chmod(executable, PRIVATE_EXECUTABLE_MODE)
        _write_runtime_contract(
            runtime_contract,
            collector=collector,
            control=control,
        )
        _write_info_plist(
            info_plist,
            screen_recording_usage_description=usage_description,
            status_item_text_diagnostic=status_item_text_diagnostic,
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


def _runtime_command(path: Path, *, name: str) -> RuntimeCommandRecord:
    if not path.is_absolute():
        raise MacOSAppBuildError(f"CONTX {name} executable must be absolute")
    if any(character in str(path) for character in "\r\n\0"):
        raise MacOSAppBuildError(f"CONTX {name} executable path is invalid")
    try:
        file_status = path.lstat()
        canonical = path.resolve(strict=True)
    except OSError as error:
        raise MacOSAppBuildError(f"CONTX {name} executable is unavailable") from error
    if path.is_symlink() or not stat.S_ISREG(file_status.st_mode):
        raise MacOSAppBuildError(
            f"CONTX {name} executable must be a regular non-symlink file"
        )
    if canonical != path:
        raise MacOSAppBuildError(f"CONTX {name} executable path must be canonical")
    if not os.access(path, os.X_OK):
        raise MacOSAppBuildError(f"CONTX {name} executable is not executable")
    return RuntimeCommandRecord(path=path, sha256=_sha256(path))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as file:
            while chunk := file.read(1024 * 1024):
                digest.update(chunk)
    except OSError as error:
        raise MacOSAppBuildError("Cannot hash a CONTX runtime executable") from error
    return digest.hexdigest()


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


def _write_runtime_contract(
    path: Path,
    *,
    collector: RuntimeCommandRecord,
    control: RuntimeCommandRecord,
) -> None:
    payload: dict[str, object] = {
        "schemaVersion": RUNTIME_CONTRACT_SCHEMA_VERSION,
        "collector": collector.as_plist(),
        "control": control.as_plist(),
    }
    with path.open("wb") as file:
        plistlib.dump(payload, file, fmt=plistlib.FMT_BINARY, sort_keys=True)
    os.chmod(path, PRIVATE_FILE_MODE)


def _write_info_plist(
    path: Path,
    *,
    screen_recording_usage_description: str,
    status_item_text_diagnostic: bool,
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
        "LSUIElement": True,
        "NSHighResolutionCapable": True,
        "NSPrincipalClass": "NSApplication",
        "NSScreenCaptureUsageDescription": screen_recording_usage_description,
    }
    if status_item_text_diagnostic:
        payload[STATUS_ITEM_TEXT_DIAGNOSTIC_INFO_KEY] = True
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
