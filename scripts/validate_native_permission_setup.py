"""Compile and exercise actual permission UI dispatch with synthetic APIs only."""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

from contx.macos_app.native_source import CONTX_APP_SWIFT_SOURCE
from contx.macos_app.runtime_release import inventory, remove_generated_tree, seal_tree

PROBE = r"""
extension AppDelegate {
    static func check(_ condition: @autoclosure () -> Bool) {
        precondition(condition(), "Permission setup regression")
    }

    static func probe() throws {
        let mode = CommandLine.arguments[2]
        let host = AppDelegate()
        host.contract = try JSONDecoder().decode(RuntimeContract.self,
            from: Data(contentsOf: URL(fileURLWithPath: CommandLine.arguments[1])))
        host.permissionSetupAvailable = true
        host.permissionStatusLine = NSTextField(labelWithString: "")
        var requests: [PermissionAction] = []
        var preflights = 0
        host.nativePermissionRequest = { requests.append($0) }
        host.nativePermissionAccess = {
            preflights += 1
            return PermissionAccess(schemaVersion: 1, screenRecording: true,
                accessibility: false, permissionsRequested: false, contentRead: false)
        }
        host.apply(viewState: .starting, status: nil)
        host.apply(status: try host.readControlStatus())
        check(host.permissionStatusLine?.stringValue.contains("indisponible") == false)
        host.apply(viewState: .error, status: nil)
        check(host.permissionStatusLine?.stringValue.contains("indisponible") == true)
        host.apply(status: try host.readControlStatus())
        check(host.permissionStatusLine?.stringValue.contains("indisponible") == false)
        // Simulate a stale enabled button to exercise the worker's fresh state guard.
        host.permissionSetupAvailable = true
        // No permission API is invoked by initialization or a status refresh.
        _ = try host.readControlStatus()
        check(requests.isEmpty && preflights == 0)
        if mode == "invalid" {
            for payload in ["{}", "not-json",
                "{\"schema_version\":2,\"screen_recording\":true,"
                + "\"accessibility\":false,\"permissions_requested\":false,"
                + "\"content_read\":false}"] {
                do {
                    _ = try PermissionAccess.decode(CommandResult(status: 0,
                        stdout: Data(payload.utf8)))
                    preconditionFailure("Malformed response accepted")
                } catch { }
            }
        }
        let action: PermissionAction = mode == "request" ? .screenRecording
            : mode == "accessibility" ? .accessibility : .verify
        host.beginPermissionAction(action)
        // A repeated click during an operation is ignored.
        host.beginPermissionAction(action)
        if mode == "cancel" {
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) {
                host.cancelBoundedCommand()
            }
        }
        let deadline = Date().addingTimeInterval(9)
        while host.permissionBusy && Date() < deadline {
            RunLoop.current.run(until: Date().addingTimeInterval(0.02))
        }
        check(!host.permissionBusy)
        let message = host.permissionStatusLine!.stringValue
        if mode == "request" || mode == "accessibility" {
            check(message.hasPrefix("Demande transmise"))
        } else if ["enabled", "invalid", "timeout", "cancel"].contains(mode) {
            check(message.hasPrefix("Vérification impossible"))
        } else {
            check(message.contains("Application — Écran : autorisé"))
            check(message.contains(mode == "denied"
                ? "Collecteur — Écran : non autorisé"
                : "Collecteur — Écran : autorisé"))
        }
        if mode == "request" || mode == "accessibility" {
            check(requests.count == 1 && requests[0] == action && preflights == 0)
        } else if mode == "enabled" {
            check(requests.isEmpty && preflights == 0)
        } else {
            check(requests.isEmpty && preflights == 1)
        }
        host.commandLock.lock()
        check(host.boundedCommand == nil)
        host.commandLock.unlock()
        check(host.collector == nil)
        print("PASSED \(mode)")
    }
}
try AppDelegate.probe()
"""


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="contx-permission-synthetic-") as temp:
        root = Path(temp).resolve()
        source = root / "Permission.swift"
        source.write_text(
            CONTX_APP_SWIFT_SOURCE.split("\nif CommandLine.arguments.count == 2", 1)[0]
            + PROBE
        )
        executable = root / "probe"
        subprocess.run(
            [
                "xcrun",
                "swiftc",
                str(source),
                "-module-cache-path",
                str(root / "modules"),
                "-o",
                str(executable),
            ],
            check=True,
            timeout=120,
        )
        for mode in (
            "request",
            "accessibility",
            "verify",
            "denied",
            "enabled",
            "invalid",
            "timeout",
            "cancel",
        ):
            release = root / mode
            (release / "env/bin").mkdir(parents=True)
            (release / "optmem").mkdir()
            control = dict(
                schema_version=1,
                state="disabled",
                background_enabled=False,
                collection_paused=True,
                collector_running=False,
            )
            if mode == "enabled":
                control.update(state="paused", background_enabled=True)
            response = json.dumps(
                dict(
                    schema_version=1,
                    screen_recording=mode != "denied",
                    accessibility=False,
                    permissions_requested=False,
                    content_read=False,
                )
            )
            if mode == "invalid":
                response = "{}"
            command = f"printf '%s\\n' '{response}'"
            if mode in ("timeout", "cancel"):
                command = "exec /bin/sleep 30"
            python = release / "env/bin/python3"
            python.write_text(
                '#!/bin/sh\ncase "$4" in\n'
                f"contx.macos_app.control) printf '%s\\n' '{json.dumps(control)}' ;;\n"
                "contx.daemon.entrypoint)\n"
                '[ "$5" = "--permission-preflight" ] || exit 71\n'
                '[ "$CONTX_NATIVE_HOST" = "1" ] || exit 72\n'
                f"{command} ;;\n*) exit 73 ;;\nesac\n"
            )
            python.chmod(0o700)
            (release / "optmem/memo").write_text("synthetic")
            seal_tree(release)
            manifest = root / f"{mode}.json"
            manifest.write_text(
                json.dumps(
                    dict(
                        schemaVersion=2,
                        root=str(release),
                        sourceCommit="a" * 40,
                        lockSHA256="b" * 64,
                        pythonVersion="synthetic",
                        python="env/bin/python3",
                        optmem="optmem/memo",
                        files=[f.model_dump() for f in inventory(release)],
                    )
                )
            )
            try:
                subprocess.run(
                    [str(executable), str(manifest), mode], check=True, timeout=15
                )
            finally:
                remove_generated_tree(release)


if __name__ == "__main__":
    main()
