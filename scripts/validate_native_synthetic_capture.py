"""Exercise actual native capture dispatch against synthetic child responses."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from contx.macos_app.native_source import CONTX_APP_SWIFT_SOURCE
from contx.macos_app.runtime_release import inventory, remove_generated_tree, seal_tree

PROBE = r"""
extension AppDelegate {
    static func probe() throws {
        let host = AppDelegate()
        let mode = CommandLine.arguments[2]
        host.contract = try JSONDecoder().decode(RuntimeContract.self,
            from: Data(contentsOf: URL(fileURLWithPath: CommandLine.arguments[1])))
        host.permissionSetupAvailable = true
        host.permissionStatusLine = NSTextField(labelWithString: "")
        host.testSyntheticCapture(nil)
        let deadline = Date().addingTimeInterval(8)
        var cancelled = false
        while host.permissionBusy && Date() < deadline {
            RunLoop.current.run(until: Date().addingTimeInterval(0.02))
            if mode == "cancel" && !cancelled {
                host.commandLock.lock()
                let running = host.boundedCommand?.arguments?.contains(
                    "--synthetic-capture") == true
                host.commandLock.unlock()
                if running && FileManager.default.fileExists(
                    atPath: CommandLine.arguments[3]) {
                    host.cancelBoundedCommand(); cancelled = true
                }
            }
        }
        precondition(!host.permissionBusy, "Capture UI stayed busy")
        precondition(host.collector == nil, "Collector was created")
        let message = host.permissionStatusLine!.stringValue
        if mode == "success" {
            precondition(message.contains("Capture fictive réussie"), message)
        } else {
            precondition(message.contains("impossible"), message)
        }
        precondition(host.boundedCommand == nil, "Child remained owned")
        print("PASSED \(mode)")
    }
}
try AppDelegate.probe()
"""


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="contx-capture-synthetic-") as temp:
        root = Path(temp).resolve()
        source = root / "Capture.swift"
        source.write_text(
            CONTX_APP_SWIFT_SOURCE.split("\nif CommandLine.arguments.count == 2", 1)[
                0
            ].replace("timeout: 40.0", "timeout: 0.5")
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
        for mode in ("success", "invalid", "wrongpath", "enabled", "timeout", "cancel"):
            release = root / mode
            (release / "env/bin").mkdir(parents=True)
            (release / "optmem").mkdir()
            control = dict(
                schema_version=1,
                state="disabled",
                background_enabled=mode == "enabled",
                collection_paused=True,
                collector_running=False,
            )
            python = release / "env/bin/python3"
            evidence = root / (mode + "-target.txt")
            python.write_text(f"""#!{sys.executable}
import base64, hashlib, json, os, sys, time
from pathlib import Path
if sys.argv[4] == "contx.macos_app.control":
    print({json.dumps(control)!r})
elif sys.argv[4:6] == ["contx.daemon.entrypoint", "--synthetic-capture"]:
    assert os.environ["CONTX_NATIVE_HOST"] == "1"
    target = Path(sys.argv[6])
    Path({str(evidence)!r}).write_text(str(target))
    if {mode!r} in ("timeout", "cancel"): time.sleep(30)
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lE"
        "QVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=")
    target.write_bytes(png)
    result = dict(schema_version=1, output_path=str(target.resolve()),
        width=1, height=1,
        content_hash=hashlib.sha256(png).hexdigest(),
        focus_race_reason="focused_window_changed")
    if {mode!r} == "wrongpath": result["output_path"] = "/tmp/unrelated.png"
    print(json.dumps({{}} if {mode!r} == "invalid" else result))
else: sys.exit(71)
""")
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
                    [str(executable), str(manifest), mode, str(evidence)],
                    check=True,
                    timeout=15,
                )
                if evidence.exists():
                    assert not Path(evidence.read_text()).parent.exists()
                else:
                    assert mode == "enabled"
            finally:
                remove_generated_tree(release)


if __name__ == "__main__":
    main()
