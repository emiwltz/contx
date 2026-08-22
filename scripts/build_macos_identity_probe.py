"""Build the disposable, identity-only macOS application probe."""

from __future__ import annotations

import argparse
import os
import plistlib
import shutil
import stat
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path

BUNDLE_IDENTIFIER = "io.contx.identity-probe"
BUNDLE_NAME = "CONTX Identity Probe"
EXECUTABLE_NAME = "CONTXIdentityProbe"
PRIVATE_DIRECTORY_MODE = 0o700
PRIVATE_FILE_MODE = 0o600
PRIVATE_EXECUTABLE_MODE = 0o700

IDENTITY_PROBE_SWIFT_SOURCE = r"""import AppKit
import Foundation

private struct ProbeEvidence: Codable {
    let schemaVersion: Int
    let probeKind: String
    let bundleIdentifier: String
    let bundleName: String
    let processIdentifier: Int32
    let executablePath: String
    let permissionsRequested: Bool
    let collectionStarted: Bool
}

private final class ProbeDelegate: NSObject, NSApplicationDelegate {
    private let readyFile: URL
    private let lifetimeSeconds: TimeInterval

    init(readyFile: URL, lifetimeSeconds: TimeInterval) {
        self.readyFile = readyFile
        self.lifetimeSeconds = lifetimeSeconds
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        let bundle = Bundle.main
        let evidence = ProbeEvidence(
            schemaVersion: 1,
            probeKind: "identity_only",
            bundleIdentifier: bundle.bundleIdentifier ?? "",
            bundleName: bundle.object(
                forInfoDictionaryKey: "CFBundleName"
            ) as? String ?? "",
            processIdentifier: ProcessInfo.processInfo.processIdentifier,
            executablePath: bundle.executablePath ?? "",
            permissionsRequested: false,
            collectionStarted: false
        )

        do {
            let encoder = JSONEncoder()
            encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
            let data = try encoder.encode(evidence)
            try data.write(to: readyFile, options: .atomic)
            try FileManager.default.setAttributes(
                [.posixPermissions: 0o600],
                ofItemAtPath: readyFile.path
            )
        } catch {
            fputs("CONTX identity probe could not write private evidence.\n", stderr)
            NSApplication.shared.terminate(nil)
            return
        }

        DispatchQueue.main.asyncAfter(deadline: .now() + lifetimeSeconds) {
            NSApplication.shared.terminate(nil)
        }
    }
}

private func argumentValue(_ name: String) -> String? {
    let arguments = CommandLine.arguments
    guard let index = arguments.firstIndex(of: name), index + 1 < arguments.count else {
        return nil
    }
    return arguments[index + 1]
}

guard
    let readyPath = argumentValue("--ready-file"),
    readyPath.hasPrefix("/"),
    let lifetimeText = argumentValue("--lifetime-seconds"),
    let lifetimeSeconds = Double(lifetimeText),
    (1.0...30.0).contains(lifetimeSeconds)
else {
    fputs(
        "Usage: CONTXIdentityProbe --ready-file /absolute/path " +
            "--lifetime-seconds 1..30\n",
        stderr
    )
    exit(2)
}

let readyFile = URL(fileURLWithPath: readyPath)
var isDirectory: ObjCBool = false
guard
    FileManager.default.fileExists(
        atPath: readyFile.deletingLastPathComponent().path,
        isDirectory: &isDirectory
    ),
    isDirectory.boolValue,
    !FileManager.default.fileExists(atPath: readyFile.path)
else {
    fputs(
        "CONTX identity probe requires a new file in an existing directory.\n",
        stderr
    )
    exit(2)
}

let application = NSApplication.shared
private let delegate = ProbeDelegate(
    readyFile: readyFile,
    lifetimeSeconds: lifetimeSeconds
)
application.delegate = delegate
application.setActivationPolicy(.accessory)
application.run()
"""

_ALLOWED_SWIFT_IMPORTS = frozenset({"AppKit", "Foundation"})
_FORBIDDEN_SWIFT_TOKENS = (
    "ApplicationServices",
    "AXIsProcessTrusted",
    "AXUIElement",
    "CGPreflightScreenCaptureAccess",
    "CGRequestScreenCaptureAccess",
    "CoreGraphics",
    "NSWorkspace",
    "Quartz",
    "ScreenCaptureKit",
)


class IdentityProbeBuildError(RuntimeError):
    """Report a bounded prototype build failure."""


CommandRunner = Callable[[Sequence[str]], None]


def build_identity_probe_bundle(
    output: Path,
    *,
    platform: str = sys.platform,
    run_command: CommandRunner | None = None,
) -> Path:
    """Build one new private app bundle without launching or registering it."""
    if platform != "darwin":
        raise IdentityProbeBuildError(
            "The CONTX identity probe can be built only on macOS"
        )
    destination = _validate_destination(output)
    _validate_swift_source()
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
    executable_directory.mkdir(parents=True, mode=PRIVATE_DIRECTORY_MODE)
    os.chmod(staged_app, PRIVATE_DIRECTORY_MODE)
    os.chmod(contents, PRIVATE_DIRECTORY_MODE)
    os.chmod(executable_directory, PRIVATE_DIRECTORY_MODE)
    source_file = temporary_root / "IdentityProbe.swift"
    executable = executable_directory / EXECUTABLE_NAME
    info_plist = contents / "Info.plist"
    try:
        source_file.write_text(IDENTITY_PROBE_SWIFT_SOURCE, encoding="utf-8")
        os.chmod(source_file, PRIVATE_FILE_MODE)
        runner(
            (
                "xcrun",
                "swiftc",
                str(source_file),
                "-framework",
                "AppKit",
                "-framework",
                "Foundation",
                "-o",
                str(executable),
            )
        )
        if not executable.is_file():
            raise IdentityProbeBuildError(
                "Swift did not create the CONTX identity probe executable"
            )
        os.chmod(executable, PRIVATE_EXECUTABLE_MODE)
        _write_info_plist(info_plist)
        runner(
            (
                "/usr/bin/codesign",
                "--force",
                "--sign",
                "-",
                "--timestamp=none",
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
                str(staged_app),
            )
        )
        os.replace(staged_app, destination)
    except Exception:
        if destination.exists():
            raise IdentityProbeBuildError(
                "Identity probe build unexpectedly touched the destination"
            ) from None
        raise
    finally:
        shutil.rmtree(temporary_root, ignore_errors=True)
    return destination


def _validate_destination(output: Path) -> Path:
    if not output.is_absolute():
        raise IdentityProbeBuildError("Identity probe output must be absolute")
    if output.suffix != ".app":
        raise IdentityProbeBuildError("Identity probe output must end in .app")
    if output.exists() or output.is_symlink():
        raise IdentityProbeBuildError("Identity probe output must not exist")
    try:
        parent = output.parent.resolve(strict=True)
    except OSError as error:
        raise IdentityProbeBuildError(
            "Identity probe parent directory must already exist"
        ) from error
    if not parent.is_dir():
        raise IdentityProbeBuildError("Identity probe parent path must be a directory")
    mode = stat.S_IMODE(parent.stat().st_mode)
    if mode & 0o077:
        raise IdentityProbeBuildError(
            "Identity probe parent directory permissions are too broad"
        )
    return parent / output.name


def _validate_swift_source() -> None:
    imports = {
        line.removeprefix("import ").strip()
        for line in IDENTITY_PROBE_SWIFT_SOURCE.splitlines()
        if line.startswith("import ")
    }
    if imports != _ALLOWED_SWIFT_IMPORTS:
        raise IdentityProbeBuildError(
            "Identity probe Swift imports exceed the approved identity-only scope"
        )
    if any(token in IDENTITY_PROBE_SWIFT_SOURCE for token in _FORBIDDEN_SWIFT_TOKENS):
        raise IdentityProbeBuildError(
            "Identity probe source contains a permission or collection API"
        )


def _write_info_plist(path: Path) -> None:
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
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise IdentityProbeBuildError(
            f"Identity probe tool failed to run: {command[0]}"
        ) from error
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        if len(detail) > 500:
            detail = detail[:500] + "..."
        suffix = "" if not detail else f": {detail}"
        raise IdentityProbeBuildError(
            f"Identity probe tool failed: {command[0]}{suffix}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Build a signed, identity-only macOS app probe. The command does "
            "not launch CONTX, request permissions, or collect activity."
        )
    )
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()
    try:
        bundle = build_identity_probe_bundle(arguments.output)
    except (IdentityProbeBuildError, OSError) as error:
        parser.exit(2, f"identity probe build failed: {error}\n")
    print(f"identity probe bundle: {bundle}")
    print(f"bundle identifier: {BUNDLE_IDENTIFIER}")
    print("permissions requested: no")
    print("collection started: no")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
