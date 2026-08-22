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
from dataclasses import dataclass
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


class MacOSProbeBuildError(RuntimeError):
    """Report a bounded macOS prototype build failure."""


IdentityProbeBuildError = MacOSProbeBuildError


CommandRunner = Callable[[Sequence[str]], None]


@dataclass(frozen=True, slots=True)
class ProbeBundleSpec:
    """Describe one private, disposable macOS probe bundle."""

    bundle_identifier: str
    bundle_name: str
    executable_name: str
    swift_source: str
    frameworks: tuple[str, ...]
    resources: tuple[tuple[str, bytes], ...] = ()
    info_plist_values: tuple[tuple[str, object], ...] = ()


def build_identity_probe_bundle(
    output: Path,
    *,
    platform: str = sys.platform,
    run_command: CommandRunner | None = None,
) -> Path:
    """Build one new private app bundle without launching or registering it."""
    _validate_swift_source()
    return build_macos_probe_bundle(
        output,
        spec=ProbeBundleSpec(
            bundle_identifier=BUNDLE_IDENTIFIER,
            bundle_name=BUNDLE_NAME,
            executable_name=EXECUTABLE_NAME,
            swift_source=IDENTITY_PROBE_SWIFT_SOURCE,
            frameworks=("AppKit", "Foundation"),
        ),
        platform=platform,
        run_command=run_command,
    )


def build_macos_probe_bundle(
    output: Path,
    *,
    spec: ProbeBundleSpec,
    platform: str = sys.platform,
    run_command: CommandRunner | None = None,
) -> Path:
    """Atomically build and ad-hoc sign one private macOS probe bundle."""
    if platform != "darwin":
        raise MacOSProbeBuildError("A macOS probe can be built only on macOS")
    _validate_bundle_spec(spec)
    destination = _validate_destination(output)
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
    executable = executable_directory / spec.executable_name
    info_plist = contents / "Info.plist"
    try:
        source_file.write_text(spec.swift_source, encoding="utf-8")
        os.chmod(source_file, PRIVATE_FILE_MODE)
        compiler_command = ["xcrun", "swiftc", str(source_file)]
        for framework in spec.frameworks:
            compiler_command.extend(("-framework", framework))
        compiler_command.extend(("-o", str(executable)))
        runner(tuple(compiler_command))
        if not executable.is_file():
            raise MacOSProbeBuildError(
                "Swift did not create the macOS probe executable"
            )
        os.chmod(executable, PRIVATE_EXECUTABLE_MODE)
        _write_resources(contents, spec.resources)
        _write_info_plist(info_plist, spec)
        runner(
            (
                "/usr/bin/codesign",
                "--force",
                "--sign",
                "-",
                "--timestamp=none",
                "--identifier",
                spec.bundle_identifier,
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
            raise MacOSProbeBuildError(
                "macOS probe build unexpectedly touched the destination"
            ) from None
        raise
    finally:
        shutil.rmtree(temporary_root, ignore_errors=True)
    return destination


def _validate_bundle_spec(spec: ProbeBundleSpec) -> None:
    text_values = (
        spec.bundle_identifier,
        spec.bundle_name,
        spec.executable_name,
        *spec.frameworks,
    )
    if any(
        not value or any(character in value for character in "\r\n\0")
        for value in text_values
    ):
        raise MacOSProbeBuildError("macOS probe specification contains invalid text")
    if not spec.swift_source.strip() or "\0" in spec.swift_source:
        raise MacOSProbeBuildError("macOS probe Swift source is invalid")
    if any(character in spec.executable_name for character in "/:"):
        raise MacOSProbeBuildError("macOS probe executable name is invalid")
    if any(character in spec.bundle_identifier for character in "/:"):
        raise MacOSProbeBuildError("macOS probe bundle identifier is invalid")
    resource_names = [name for name, _content in spec.resources]
    if len(resource_names) != len(set(resource_names)):
        raise MacOSProbeBuildError("macOS probe resource names must be unique")
    if any(
        not name or name != Path(name).name or name.startswith(".")
        for name in resource_names
    ):
        raise MacOSProbeBuildError("macOS probe resource name is invalid")
    info_keys = [key for key, _value in spec.info_plist_values]
    if len(info_keys) != len(set(info_keys)):
        raise MacOSProbeBuildError("macOS probe Info.plist keys must be unique")
    if any(
        not key or any(character in key for character in "\r\n\0") for key in info_keys
    ):
        raise MacOSProbeBuildError("macOS probe Info.plist key is invalid")


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


def _write_resources(
    contents: Path,
    resources: tuple[tuple[str, bytes], ...],
) -> None:
    if not resources:
        return
    resources_directory = contents / "Resources"
    resources_directory.mkdir(mode=PRIVATE_DIRECTORY_MODE)
    os.chmod(resources_directory, PRIVATE_DIRECTORY_MODE)
    for name, content in resources:
        resource = resources_directory / name
        resource.write_bytes(content)
        os.chmod(resource, PRIVATE_FILE_MODE)


def _write_info_plist(path: Path, spec: ProbeBundleSpec) -> None:
    payload: dict[str, object] = {
        "CFBundleDevelopmentRegion": "en",
        "CFBundleDisplayName": spec.bundle_name,
        "CFBundleExecutable": spec.executable_name,
        "CFBundleIdentifier": spec.bundle_identifier,
        "CFBundleInfoDictionaryVersion": "6.0",
        "CFBundleName": spec.bundle_name,
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": "0.0.1",
        "CFBundleVersion": "1",
        "LSMinimumSystemVersion": "14.0",
        "LSUIElement": True,
        "NSHighResolutionCapable": True,
        "NSPrincipalClass": "NSApplication",
    }
    for key, value in spec.info_plist_values:
        if key in payload:
            raise MacOSProbeBuildError(f"macOS probe Info.plist cannot override {key}")
        payload[key] = value
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
