"""Compile the real Swift shutdown path and exercise disposable synthetic children.

This validator opens no window, reads no CONTX runtime, and starts no collector.
It uses only temporary files and /bin/sleep children owned by the test process.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from contx.macos_app.native_source import CONTX_APP_SWIFT_SOURCE

SHUTDOWN_PROBE = r"""
extension AppDelegate {
    static func validateShutdown() throws {
        let host = AppDelegate()
        // Closing and Cmd-Q both enter this same application termination hook.
        let child = Process()
        child.executableURL = URL(fileURLWithPath: "/bin/sleep")
        child.arguments = ["30"]
        try child.run()
        host.collector = child
        precondition(child.isRunning)
        host.applicationWillTerminate(Notification(name: Notification.Name("test")))
        precondition(!child.isRunning, "Termination left a child alive")
        precondition(host.collector == nil)
        precondition(host.shuttingDown)
        // Repeated shutdown must not signal an unrelated or stale PID.
        host.applicationWillTerminate(Notification(name: Notification.Name("test")))
        // A child that already exited must also be cleared safely.
        let exited = Process()
        exited.executableURL = URL(fileURLWithPath: "/usr/bin/true")
        try exited.run()
        exited.waitUntilExit()
        host.collector = exited
        host.applicationWillTerminate(Notification(name: Notification.Name("test")))
        precondition(host.collector == nil)
        print("Native shutdown: running child stopped; " +
              "repeated shutdown safe; exited child cleared")
    }
}
try AppDelegate.validateShutdown()
"""


def main() -> None:
    source, separator, _ = CONTX_APP_SWIFT_SOURCE.partition(
        "\nlet application = NSApplication.shared\n"
    )
    if not separator:
        raise RuntimeError("Native app launch boundary not found")
    with tempfile.TemporaryDirectory(prefix="contx-shutdown-") as temporary:
        root = Path(temporary)
        swift = root / "Shutdown.swift"
        executable = root / "shutdown-test"
        swift.write_text(source + SHUTDOWN_PROBE, encoding="utf-8")
        subprocess.run(
            [
                "xcrun",
                "swiftc",
                str(swift),
                "-module-cache-path",
                str(root / "module-cache"),
                "-framework",
                "AppKit",
                "-framework",
                "CryptoKit",
                "-framework",
                "Foundation",
                "-o",
                str(executable),
            ],
            check=True,
            timeout=120,
        )
        subprocess.run([str(executable)], check=True, timeout=25)


if __name__ == "__main__":
    main()
