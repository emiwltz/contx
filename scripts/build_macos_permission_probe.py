"""Build the gated, non-capturing macOS permission-attribution probe."""

from __future__ import annotations

import argparse
import base64
import hashlib
import os
import sys
from pathlib import Path

from scripts.build_macos_identity_probe import (
    BUNDLE_IDENTIFIER,
    BUNDLE_NAME,
    EXECUTABLE_NAME,
    CommandRunner,
    MacOSProbeBuildError,
    ProbeBundleSpec,
    build_macos_probe_bundle,
)

REQUEST_CONFIRMATION = "REQUEST CONTX IDENTITY PROBE SCREEN RECORDING"
VERIFY_CONFIRMATION = "VERIFY CONTX IDENTITY PROBE SCREEN RECORDING"
PERMISSION_CHILD_NAME = "permission_child.py"

PERMISSION_SWIFT_SOURCE_TEMPLATE = r"""import AppKit
import CoreGraphics
import CryptoKit
import Foundation

private let requestConfirmation =
    "REQUEST CONTX IDENTITY PROBE SCREEN RECORDING"
private let verifyConfirmation =
    "VERIFY CONTX IDENTITY PROBE SCREEN RECORDING"
private let pythonExecutableBase64 = "__PYTHON_EXECUTABLE_BASE64__"
private let pythonExecutableSha256 = "__PYTHON_EXECUTABLE_SHA256__"

private enum ProbeMode: String {
    case identity
    case request
    case verify
}

private struct ProbeEvidence: Codable {
    let schemaVersion: Int
    let probeKind: String
    let bundleIdentifier: String
    let bundleName: String
    let processIdentifier: Int32
    let mode: String
    let permissionsRequested: Bool
    let permissionApiInvoked: Bool
    let nativePreflight: Bool?
    let requestReturned: Bool?
    let childLaunched: Bool
    let childExitStatus: Int32?
    let pixelsRead: Bool
    let collectionStarted: Bool
}

private final class ProbeDelegate: NSObject, NSApplicationDelegate {
    private let mode: ProbeMode
    private let evidenceFile: URL
    private let childEvidenceFile: URL?
    private let lifetimeSeconds: TimeInterval

    init(
        mode: ProbeMode,
        evidenceFile: URL,
        childEvidenceFile: URL?,
        lifetimeSeconds: TimeInterval
    ) {
        self.mode = mode
        self.evidenceFile = evidenceFile
        self.childEvidenceFile = childEvidenceFile
        self.lifetimeSeconds = lifetimeSeconds
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        switch mode {
        case .identity:
            finish(
                nativePreflight: nil,
                requestReturned: nil,
                childLaunched: false,
                childExitStatus: nil,
                keepAlive: true
            )
        case .request:
            let beforeRequest = CGPreflightScreenCaptureAccess()
            let requestResult = CGRequestScreenCaptureAccess()
            finish(
                nativePreflight: beforeRequest,
                requestReturned: requestResult,
                childLaunched: false,
                childExitStatus: nil,
                keepAlive: false
            )
        case .verify:
            let nativeResult = CGPreflightScreenCaptureAccess()
            let childStatus = runPythonChild()
            finish(
                nativePreflight: nativeResult,
                requestReturned: nil,
                childLaunched: childStatus != nil,
                childExitStatus: childStatus,
                keepAlive: false
            )
        }
    }

    private func runPythonChild() -> Int32? {
        guard
            let childEvidenceFile,
            let childScript = Bundle.main.url(
                forResource: "permission_child",
                withExtension: "py"
            ),
            let pythonData = Data(base64Encoded: pythonExecutableBase64),
            let pythonExecutable = String(data: pythonData, encoding: .utf8),
            FileManager.default.isExecutableFile(atPath: pythonExecutable),
            let executableData = try? Data(
                contentsOf: URL(fileURLWithPath: pythonExecutable)
            ),
            sha256Hex(executableData) == pythonExecutableSha256
        else {
            return nil
        }

        let process = Process()
        process.executableURL = URL(fileURLWithPath: pythonExecutable)
        process.arguments = [
            childScript.path,
            "--output",
            childEvidenceFile.path,
        ]
        process.environment = [
            "PATH": "/usr/bin:/bin",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
        ]
        process.standardOutput = FileHandle.nullDevice
        process.standardError = FileHandle.nullDevice
        let completed = DispatchSemaphore(value: 0)
        process.terminationHandler = { _process in completed.signal() }
        do {
            try process.run()
        } catch {
            return nil
        }
        if completed.wait(timeout: .now() + 5.0) == .timedOut {
            process.terminate()
            _ = completed.wait(timeout: .now() + 1.0)
            return 124
        }
        return process.terminationStatus
    }

    private func finish(
        nativePreflight: Bool?,
        requestReturned: Bool?,
        childLaunched: Bool,
        childExitStatus: Int32?,
        keepAlive: Bool
    ) {
        let bundle = Bundle.main
        let evidence = ProbeEvidence(
            schemaVersion: 1,
            probeKind: "permission_attribution",
            bundleIdentifier: bundle.bundleIdentifier ?? "",
            bundleName: bundle.object(
                forInfoDictionaryKey: "CFBundleName"
            ) as? String ?? "",
            processIdentifier: ProcessInfo.processInfo.processIdentifier,
            mode: mode.rawValue,
            permissionsRequested: mode == .request,
            permissionApiInvoked: mode != .identity,
            nativePreflight: nativePreflight,
            requestReturned: requestReturned,
            childLaunched: childLaunched,
            childExitStatus: childExitStatus,
            pixelsRead: false,
            collectionStarted: false
        )
        do {
            try writePrivateEvidence(evidence, to: evidenceFile)
        } catch {
            fputs(
                "CONTX permission probe could not write private evidence.\n",
                stderr
            )
            NSApplication.shared.terminate(nil)
            return
        }
        if keepAlive {
            DispatchQueue.main.asyncAfter(
                deadline: .now() + lifetimeSeconds
            ) {
                NSApplication.shared.terminate(nil)
            }
        } else {
            NSApplication.shared.terminate(nil)
        }
    }
}

private func argumentValue(_ name: String) -> String? {
    let arguments = CommandLine.arguments
    guard
        let index = arguments.firstIndex(of: name),
        index + 1 < arguments.count
    else {
        return nil
    }
    return arguments[index + 1]
}

private func sha256Hex(_ data: Data) -> String {
    SHA256.hash(data: data).map {
        String(format: "%02x", $0)
    }.joined()
}

private func isNewPrivateFile(_ url: URL) -> Bool {
    guard url.path.hasPrefix("/") else {
        return false
    }
    let parent = url.deletingLastPathComponent()
    var isDirectory: ObjCBool = false
    guard
        FileManager.default.fileExists(
            atPath: parent.path,
            isDirectory: &isDirectory
        ),
        isDirectory.boolValue,
        !FileManager.default.fileExists(atPath: url.path),
        let attributes = try? FileManager.default.attributesOfItem(
            atPath: parent.path
        ),
        let permissions = attributes[.posixPermissions] as? NSNumber
    else {
        return false
    }
    return permissions.intValue & 0o077 == 0
}

private func writePrivateEvidence(
    _ evidence: ProbeEvidence,
    to output: URL
) throws {
    let encoder = JSONEncoder()
    encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
    let data = try encoder.encode(evidence)
    try data.write(to: output, options: .atomic)
    try FileManager.default.setAttributes(
        [.posixPermissions: 0o600],
        ofItemAtPath: output.path
    )
}

guard
    let modeText = argumentValue("--mode"),
    let mode = ProbeMode(rawValue: modeText),
    let evidencePath = argumentValue("--evidence-file"),
    let lifetimeText = argumentValue("--lifetime-seconds"),
    let lifetimeSeconds = Double(lifetimeText),
    (1.0...30.0).contains(lifetimeSeconds)
else {
    fputs("CONTX permission probe arguments are invalid.\n", stderr)
    exit(2)
}

let evidenceFile = URL(fileURLWithPath: evidencePath)
guard isNewPrivateFile(evidenceFile) else {
    fputs("CONTX permission probe evidence path is unsafe.\n", stderr)
    exit(2)
}

let confirmation = argumentValue("--confirmation")
let childEvidenceFile = argumentValue("--child-evidence-file").map {
    URL(fileURLWithPath: $0)
}

switch mode {
case .identity:
    guard confirmation == nil, childEvidenceFile == nil else {
        fputs("Identity mode accepts no sensitive arguments.\n", stderr)
        exit(2)
    }
case .request:
    guard confirmation == requestConfirmation, childEvidenceFile == nil else {
        fputs("Screen Recording request confirmation is invalid.\n", stderr)
        exit(2)
    }
case .verify:
    guard
        confirmation == verifyConfirmation,
        let childEvidenceFile,
        childEvidenceFile.standardizedFileURL.path
            != evidenceFile.standardizedFileURL.path,
        isNewPrivateFile(childEvidenceFile)
    else {
        fputs("Screen Recording verification arguments are invalid.\n", stderr)
        exit(2)
    }
}

let application = NSApplication.shared
private let delegate = ProbeDelegate(
    mode: mode,
    evidenceFile: evidenceFile,
    childEvidenceFile: childEvidenceFile,
    lifetimeSeconds: lifetimeSeconds
)
application.delegate = delegate
application.setActivationPolicy(.accessory)
application.run()
"""

PERMISSION_CHILD_SOURCE = r'''"""Check one existing permission without requesting
or capturing content."""

from __future__ import annotations

import argparse
import json
import os
import stat
from pathlib import Path

import Quartz


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()
    output = arguments.output
    if not output.is_absolute() or output.exists() or output.is_symlink():
        parser.exit(2, "permission child output path is unsafe\n")
    try:
        parent = output.parent.resolve(strict=True)
    except OSError:
        parser.exit(2, "permission child parent is unavailable\n")
    if not parent.is_dir() or stat.S_IMODE(parent.stat().st_mode) & 0o077:
        parser.exit(2, "permission child parent is not private\n")
    available = bool(Quartz.CGPreflightScreenCaptureAccess())
    payload = {
        "collection_started": False,
        "permissions_requested": False,
        "pixels_read": False,
        "probe_kind": "python_child_preflight",
        "schema_version": 1,
        "screen_recording_available": available,
    }
    descriptor = os.open(
        output,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    with os.fdopen(descriptor, "w", encoding="utf-8") as file:
        json.dump(payload, file, indent=2, sort_keys=True)
        file.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''

_ALLOWED_SWIFT_IMPORTS = frozenset(
    {"AppKit", "CoreGraphics", "CryptoKit", "Foundation"}
)
_FORBIDDEN_SWIFT_TOKENS = (
    "ApplicationServices",
    "AXIsProcessTrusted",
    "AXUIElement",
    "CGCaptureAllDisplays",
    "CGDisplayCreateImage",
    "CGWindowListCreateImage",
    "NSWorkspace",
    "SCScreenshotManager",
    "ScreenCaptureKit",
)
_FORBIDDEN_CHILD_TOKENS = (
    "CGRequestScreenCaptureAccess",
    "ScreenCaptureKit",
    "contx.",
    "requests",
    "socket",
    "subprocess",
    "urllib",
)


def build_permission_attribution_probe_bundle(
    output: Path,
    *,
    python_executable: Path,
    usage_description: str,
    platform: str = sys.platform,
    run_command: CommandRunner | None = None,
) -> Path:
    """Build the gated probe without launching or invoking permission APIs."""
    python_path = _validate_python_executable(python_executable)
    description = _validate_usage_description(usage_description)
    encoded_python = base64.b64encode(os.fsencode(str(python_path))).decode("ascii")
    python_digest = hashlib.sha256(python_path.read_bytes()).hexdigest()
    swift_source = PERMISSION_SWIFT_SOURCE_TEMPLATE.replace(
        "__PYTHON_EXECUTABLE_BASE64__",
        encoded_python,
    ).replace(
        "__PYTHON_EXECUTABLE_SHA256__",
        python_digest,
    )
    _validate_sources(swift_source)
    spec = ProbeBundleSpec(
        bundle_identifier=BUNDLE_IDENTIFIER,
        bundle_name=BUNDLE_NAME,
        executable_name=EXECUTABLE_NAME,
        swift_source=swift_source,
        frameworks=("AppKit", "CoreGraphics", "Foundation"),
        resources=((PERMISSION_CHILD_NAME, PERMISSION_CHILD_SOURCE.encode("utf-8")),),
        info_plist_values=(("NSScreenCaptureUsageDescription", description),),
    )
    return build_macos_probe_bundle(
        output,
        spec=spec,
        platform=platform,
        run_command=run_command,
    )


def _validate_python_executable(path: Path) -> Path:
    if not path.is_absolute():
        raise MacOSProbeBuildError("Permission probe Python path must be absolute")
    if any(character in str(path) for character in "\r\n\0"):
        raise MacOSProbeBuildError("Permission probe Python path is invalid")
    if not path.is_file() or not os.access(path, os.X_OK):
        raise MacOSProbeBuildError(
            "Permission probe Python path must be an executable file"
        )
    return path


def _validate_usage_description(value: str) -> str:
    description = value.strip()
    if description != value or not 40 <= len(description) <= 240:
        raise MacOSProbeBuildError(
            "Screen Recording usage description must contain 40 to 240 characters"
        )
    if any(character in description for character in "\r\n\0"):
        raise MacOSProbeBuildError(
            "Screen Recording usage description contains invalid characters"
        )
    return description


def _validate_sources(swift_source: str) -> None:
    imports = {
        line.removeprefix("import ").strip()
        for line in swift_source.splitlines()
        if line.startswith("import ")
    }
    if imports != _ALLOWED_SWIFT_IMPORTS:
        raise MacOSProbeBuildError(
            "Permission probe Swift imports exceed the approved scope"
        )
    if any(token in swift_source for token in _FORBIDDEN_SWIFT_TOKENS):
        raise MacOSProbeBuildError(
            "Permission probe Swift source contains a capture API"
        )
    if swift_source.count("CGRequestScreenCaptureAccess()") != 1:
        raise MacOSProbeBuildError(
            "Permission probe must contain exactly one gated request call"
        )
    if any(token in PERMISSION_CHILD_SOURCE for token in _FORBIDDEN_CHILD_TOKENS):
        raise MacOSProbeBuildError(
            "Permission probe child source exceeds the preflight-only scope"
        )
    if PERMISSION_CHILD_SOURCE.count("CGPreflightScreenCaptureAccess()") != 1:
        raise MacOSProbeBuildError(
            "Permission probe child must contain exactly one preflight call"
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Build the gated CONTX Screen Recording attribution probe. This "
            "command does not launch the app or invoke a permission API."
        )
    )
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--python-executable", required=True, type=Path)
    parser.add_argument("--usage-description", required=True)
    arguments = parser.parse_args()
    try:
        bundle = build_permission_attribution_probe_bundle(
            arguments.output,
            python_executable=arguments.python_executable,
            usage_description=arguments.usage_description,
        )
    except (MacOSProbeBuildError, OSError) as error:
        parser.exit(2, f"permission probe build failed: {error}\n")
    print(f"permission probe bundle: {bundle}")
    print(f"bundle identifier: {BUNDLE_IDENTIFIER}")
    print(f"request confirmation: {REQUEST_CONFIRMATION}")
    print(f"verify confirmation: {VERIFY_CONFIRMATION}")
    print("permission API invoked: no")
    print("collection started: no")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
