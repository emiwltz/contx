"""Auditable Swift source for the signed CONTX menu-bar host."""

from __future__ import annotations

CONTX_APP_SWIFT_SOURCE = r"""import AppKit
import CryptoKit
import Darwin
import Foundation

private let contractSchemaVersion = 1
private let controlSchemaVersion = 1
private let controlTimeoutSeconds: TimeInterval = 5.0
private let collectorStopTimeoutSeconds: TimeInterval = 10.0
private let maximumControlOutputBytes = 32 * 1024
private let statusItemAutosaveName = "io.contx.desktop.status-item"
private let statusItemIdentifier = "io.contx.desktop.status-item.button"
#if CONTX_STATUS_ITEM_TEXT_DIAGNOSTIC
private let statusItemLength = NSStatusItem.variableLength
private let statusItemTextDiagnosticLabel = "CONTX TEST"
#else
private let statusItemLength = NSStatusItem.squareLength
#endif

private struct RuntimeCommand: Decodable {
    let path: String
    let sha256: String
}

private struct RuntimeContract: Decodable {
    let schemaVersion: Int
    let collector: RuntimeCommand
    let control: RuntimeCommand
}

private enum ControlState: String, Decodable {
    case disabled
    case stopped
    case paused
    case active
}

private struct ControlStatus: Decodable {
    let schemaVersion: Int
    let state: ControlState
    let backgroundEnabled: Bool
    let collectionPaused: Bool
    let collectorRunning: Bool

    private enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case state
        case backgroundEnabled = "background_enabled"
        case collectionPaused = "collection_paused"
        case collectorRunning = "collector_running"
    }
}

private struct CommandResult {
    let status: Int32
    let stdout: Data
}

private enum HostFailure: Error {
    case invalidBundle
    case invalidContract
    case invalidExecutable
    case changedExecutable
    case commandFailed
    case commandTimedOut
    case invalidControlResponse
    case unavailableStatusItem
}

private enum HostViewState {
    case starting
    case disabled
    case stopped
    case paused
    case active
    case error

    var symbolName: String {
        switch self {
        case .starting:
            return "circle.dotted"
        case .disabled:
            return "circle.slash"
        case .stopped:
            return "exclamationmark.circle"
        case .paused:
            return "pause.circle.fill"
        case .active:
            return "record.circle.fill"
        case .error:
            return "exclamationmark.triangle.fill"
        }
    }

    var statusText: String {
        switch self {
        case .starting:
            return "Collector starting"
        case .disabled:
            return "Collection disabled"
        case .stopped:
            return "Collector stopped"
        case .paused:
            return "Collection paused"
        case .active:
            return "Collection active"
        case .error:
            return "Collector error"
        }
    }
}

private final class AppDelegate: NSObject, NSApplicationDelegate {
    private let worker = DispatchQueue(label: "io.contx.desktop.runtime")
    private var contract: RuntimeContract?
    private var collector: Process?
    private var shuttingDown = false
    private var refreshPending = false
    private var statusItem: NSStatusItem?
    private var statusLine: NSMenuItem?
    private var pauseFifteenItem: NSMenuItem?
    private var pauseIndefinitelyItem: NSMenuItem?
    private var resumeItem: NSMenuItem?
    private var restartItem: NSMenuItem?
    private var timer: Timer?

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApplication.shared.setActivationPolicy(.accessory)
        guard NSApplication.shared.activationPolicy() == .accessory else {
            NSApplication.shared.terminate(nil)
            return
        }
        do {
            try buildMenu()
        } catch {
            NSApplication.shared.terminate(nil)
            return
        }
        apply(viewState: .starting, status: nil)
        do {
            contract = try loadRuntimeContract()
        } catch {
            apply(viewState: .error, status: nil)
            return
        }
        timer = Timer.scheduledTimer(
            timeInterval: 1.0,
            target: self,
            selector: #selector(refreshTimerFired(_:)),
            userInfo: nil,
            repeats: true
        )
        refreshAndStartIfNeeded()
    }

    func applicationWillTerminate(_ notification: Notification) {
        timer?.invalidate()
        timer = nil
        shuttingDown = true
        worker.sync {
            stopCollector()
        }
        if let statusItem {
            NSStatusBar.system.removeStatusItem(statusItem)
        }
        statusItem = nil
    }

    @objc private func refreshTimerFired(_ timer: Timer) {
        refreshStatus()
    }

    @objc private func pauseForFifteenMinutes(_ sender: Any?) {
        performControl(arguments: ["pause", "--seconds", "900"])
    }

    @objc private func pauseIndefinitely(_ sender: Any?) {
        performControl(arguments: ["pause"])
    }

    @objc private func resumeCollection(_ sender: Any?) {
        performControl(arguments: ["resume"])
    }

    @objc private func restartCollector(_ sender: Any?) {
        worker.async { [weak self] in
            guard let self, self.collector == nil else {
                return
            }
            do {
                let status = try self.readControlStatus()
                guard
                    status.backgroundEnabled,
                    status.collectionPaused,
                    !status.collectorRunning
                else {
                    throw HostFailure.commandFailed
                }
                try self.startCollector()
                DispatchQueue.main.async {
                    self.apply(viewState: .starting, status: status)
                }
            } catch {
                DispatchQueue.main.async {
                    self.apply(viewState: .error, status: nil)
                }
            }
        }
    }

    private func buildMenu() throws {
        let item = NSStatusBar.system.statusItem(
            withLength: statusItemLength
        )
        item.autosaveName = NSStatusItem.AutosaveName(statusItemAutosaveName)
        item.length = statusItemLength
        item.isVisible = true
        guard item.statusBar != nil, item.isVisible, let button = item.button else {
            NSStatusBar.system.removeStatusItem(item)
            throw HostFailure.unavailableStatusItem
        }
        button.identifier = NSUserInterfaceItemIdentifier(statusItemIdentifier)
        let menu = NSMenu()
        let status = NSMenuItem(
            title: "Collection status",
            action: nil,
            keyEquivalent: ""
        )
        status.isEnabled = false
        menu.addItem(status)
        menu.addItem(.separator())

        let pauseFifteen = menuItem(
            title: "Pause for 15 minutes",
            action: #selector(pauseForFifteenMinutes(_:))
        )
        let pauseIndefinitely = menuItem(
            title: "Pause indefinitely",
            action: #selector(pauseIndefinitely(_:))
        )
        let resume = menuItem(
            title: "Resume collection",
            action: #selector(resumeCollection(_:))
        )
        let restart = menuItem(
            title: "Restart collector while paused",
            action: #selector(restartCollector(_:))
        )
        menu.addItem(pauseFifteen)
        menu.addItem(pauseIndefinitely)
        menu.addItem(resume)
        menu.addItem(.separator())
        menu.addItem(restart)
        item.menu = menu

        statusItem = item
        statusLine = status
        pauseFifteenItem = pauseFifteen
        pauseIndefinitelyItem = pauseIndefinitely
        resumeItem = resume
        restartItem = restart
    }

    private func menuItem(title: String, action: Selector) -> NSMenuItem {
        let item = NSMenuItem(title: title, action: action, keyEquivalent: "")
        item.target = self
        return item
    }

    private func refreshAndStartIfNeeded() {
        worker.async { [weak self] in
            guard let self else {
                return
            }
            do {
                let status = try self.readControlStatus()
                if
                    status.backgroundEnabled,
                    status.collectorRunning,
                    self.collector == nil
                {
                    throw HostFailure.commandFailed
                } else if status.backgroundEnabled && self.collector == nil {
                    try self.startCollector()
                    DispatchQueue.main.async {
                        self.apply(viewState: .starting, status: status)
                    }
                } else {
                    DispatchQueue.main.async {
                        self.apply(status: status)
                    }
                }
            } catch {
                DispatchQueue.main.async {
                    self.apply(viewState: .error, status: nil)
                }
            }
        }
    }

    private func refreshStatus() {
        guard !refreshPending else {
            return
        }
        refreshPending = true
        worker.async { [weak self] in
            guard let self else {
                return
            }
            do {
                let status = try self.readControlStatus()
                if !status.backgroundEnabled && self.collector != nil {
                    self.stopCollector()
                }
                DispatchQueue.main.async {
                    self.refreshPending = false
                    self.apply(status: status)
                }
            } catch {
                DispatchQueue.main.async {
                    self.refreshPending = false
                    self.apply(viewState: .error, status: nil)
                }
            }
        }
    }

    private func performControl(arguments: [String]) {
        worker.async { [weak self] in
            guard let self else {
                return
            }
            do {
                let status = try self.runControl(arguments: arguments)
                DispatchQueue.main.async {
                    self.apply(status: status)
                }
            } catch {
                DispatchQueue.main.async {
                    self.apply(viewState: .error, status: nil)
                }
            }
        }
    }

    private func readControlStatus() throws -> ControlStatus {
        try runControl(arguments: ["status"])
    }

    private func runControl(arguments: [String]) throws -> ControlStatus {
        guard let contract else {
            throw HostFailure.invalidContract
        }
        let result = try runBoundedCommand(
            contract.control,
            arguments: arguments,
            nativeHostChild: false
        )
        guard result.status == 0 else {
            throw HostFailure.commandFailed
        }
        let status: ControlStatus
        do {
            status = try JSONDecoder().decode(ControlStatus.self, from: result.stdout)
        } catch {
            throw HostFailure.invalidControlResponse
        }
        guard status.schemaVersion == controlSchemaVersion else {
            throw HostFailure.invalidControlResponse
        }
        return status
    }

    private func startCollector() throws {
        guard collector == nil, let contract else {
            throw HostFailure.invalidContract
        }
        try verify(command: contract.collector)
        let process = Process()
        process.executableURL = URL(fileURLWithPath: contract.collector.path)
        process.arguments = []
        process.environment = minimalEnvironment(nativeHostChild: true)
        process.standardOutput = FileHandle.standardOutput
        process.standardError = FileHandle.standardError
        process.terminationHandler = { [weak self] terminated in
            self?.worker.async {
                self?.collectorDidTerminate(terminated)
            }
        }
        collector = process
        do {
            try process.run()
        } catch {
            collector = nil
            throw HostFailure.commandFailed
        }
        guard process.isRunning else {
            collector = nil
            throw HostFailure.commandFailed
        }
    }

    private func collectorDidTerminate(_ terminated: Process) {
        guard collector === terminated else {
            return
        }
        collector = nil
        if shuttingDown {
            return
        }
        _ = try? runControl(arguments: ["pause"])
        DispatchQueue.main.async { [weak self] in
            self?.apply(viewState: .error, status: nil)
        }
    }

    private func stopCollector() {
        guard let process = collector else {
            return
        }
        collector = nil
        if process.isRunning {
            process.terminate()
            if !waitForExit(process, timeout: collectorStopTimeoutSeconds) {
                kill(process.processIdentifier, SIGKILL)
                _ = waitForExit(process, timeout: 1.0)
            }
        }
    }

    private func runBoundedCommand(
        _ command: RuntimeCommand,
        arguments: [String],
        nativeHostChild: Bool
    ) throws -> CommandResult {
        try verify(command: command)
        let process = Process()
        let standardOutput = Pipe()
        let standardError = Pipe()
        process.executableURL = URL(fileURLWithPath: command.path)
        process.arguments = arguments
        process.environment = minimalEnvironment(nativeHostChild: nativeHostChild)
        process.standardOutput = standardOutput
        process.standardError = standardError
        do {
            try process.run()
        } catch {
            throw HostFailure.commandFailed
        }
        guard waitForExit(process, timeout: controlTimeoutSeconds) else {
            process.terminate()
            if !waitForExit(process, timeout: 1.0) {
                kill(process.processIdentifier, SIGKILL)
                _ = waitForExit(process, timeout: 1.0)
            }
            throw HostFailure.commandTimedOut
        }
        let output = standardOutput.fileHandleForReading.readDataToEndOfFile()
        _ = standardError.fileHandleForReading.readDataToEndOfFile()
        guard output.count <= maximumControlOutputBytes else {
            throw HostFailure.invalidControlResponse
        }
        return CommandResult(status: process.terminationStatus, stdout: output)
    }

    private func waitForExit(_ process: Process, timeout: TimeInterval) -> Bool {
        let deadline = Date().addingTimeInterval(timeout)
        while process.isRunning && Date() < deadline {
            Thread.sleep(forTimeInterval: 0.05)
        }
        return !process.isRunning
    }

    private func minimalEnvironment(nativeHostChild: Bool) -> [String: String] {
        var environment = [
            "HOME": NSHomeDirectory(),
            "PATH": "/usr/bin:/bin",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
            "PYTHONUNBUFFERED": "1",
            "TMPDIR": NSTemporaryDirectory(),
        ]
        if nativeHostChild {
            environment["CONTX_NATIVE_HOST"] = "1"
        }
        if
            let runtimeRoot = ProcessInfo.processInfo.environment[
                "CONTX_RUNTIME_ROOT"
            ],
            runtimeRoot.hasPrefix("/")
        {
            environment["CONTX_RUNTIME_ROOT"] = runtimeRoot
        }
        return environment
    }

    private func apply(status: ControlStatus) {
        switch status.state {
        case .disabled:
            apply(viewState: .disabled, status: status)
        case .stopped:
            apply(viewState: .stopped, status: status)
        case .paused:
            apply(viewState: .paused, status: status)
        case .active:
            apply(viewState: .active, status: status)
        }
    }

    private func apply(viewState: HostViewState, status: ControlStatus?) {
        guard let item = statusItem, let button = item.button else {
            return
        }
#if CONTX_STATUS_ITEM_TEXT_DIAGNOSTIC
        button.image = nil
        button.title = statusItemTextDiagnosticLabel
#else
        let image = NSImage(
            systemSymbolName: viewState.symbolName,
            accessibilityDescription: "CONTX — \(viewState.statusText)"
        )
        image?.isTemplate = true
        if let image {
            button.title = ""
            button.image = image
        } else {
            button.image = nil
            button.title = "●"
        }
#endif
        button.toolTip = "CONTX — \(viewState.statusText)"
        statusLine?.title = viewState.statusText

        let enabled = status?.backgroundEnabled == true
        let running = status?.collectorRunning == true
        let paused = status?.collectionPaused == true
        pauseFifteenItem?.isEnabled = enabled && running && !paused
        pauseIndefinitelyItem?.isEnabled = enabled && running && !paused
        resumeItem?.isEnabled = enabled && running && paused
        restartItem?.isEnabled = enabled && !running && paused
    }

    private func loadRuntimeContract() throws -> RuntimeContract {
        guard
            let url = Bundle.main.url(
                forResource: "RuntimeContract",
                withExtension: "plist"
            ),
            let data = try? Data(contentsOf: url),
            let contract = try? PropertyListDecoder().decode(
                RuntimeContract.self,
                from: data
            ),
            contract.schemaVersion == contractSchemaVersion
        else {
            throw HostFailure.invalidBundle
        }
        try verify(command: contract.collector)
        try verify(command: contract.control)
        return contract
    }

    private func verify(command: RuntimeCommand) throws {
        guard
            command.path.hasPrefix("/"),
            command.sha256.count == 64,
            command.sha256.allSatisfy({ $0.isHexDigit && !$0.isUppercase })
        else {
            throw HostFailure.invalidContract
        }
        let url = URL(fileURLWithPath: command.path)
        let values: URLResourceValues
        do {
            values = try url.resourceValues(
                forKeys: [.isRegularFileKey, .isSymbolicLinkKey]
            )
        } catch {
            throw HostFailure.invalidExecutable
        }
        guard
            values.isRegularFile == true,
            values.isSymbolicLink != true,
            FileManager.default.isExecutableFile(atPath: command.path)
        else {
            throw HostFailure.invalidExecutable
        }
        let data: Data
        do {
            data = try Data(contentsOf: url, options: .mappedIfSafe)
        } catch {
            throw HostFailure.invalidExecutable
        }
        let digest = SHA256.hash(data: data).map {
            String(format: "%02x", $0)
        }.joined()
        guard digest == command.sha256 else {
            throw HostFailure.changedExecutable
        }
    }
}

let application = NSApplication.shared
private let delegate = AppDelegate()
application.delegate = delegate
application.setActivationPolicy(.accessory)
application.run()
"""

ALLOWED_SWIFT_IMPORTS = frozenset({"AppKit", "CryptoKit", "Darwin", "Foundation"})
FORBIDDEN_SWIFT_TOKENS = (
    "ApplicationServices",
    "AXIsProcessTrusted",
    "AXUIElement",
    "CGPreflightScreenCaptureAccess",
    "CGRequestScreenCaptureAccess",
    "CGWindowList",
    "NSWorkspace",
    "NWConnection",
    "ScreenCaptureKit",
    "URLSession",
)


def validate_native_source() -> None:
    """Reject accidental permission, capture, observation, or network scope."""
    imports = {
        line.removeprefix("import ").strip()
        for line in CONTX_APP_SWIFT_SOURCE.splitlines()
        if line.startswith("import ")
    }
    if imports != ALLOWED_SWIFT_IMPORTS:
        raise ValueError("CONTX app Swift imports exceed the approved host scope")
    if any(token in CONTX_APP_SWIFT_SOURCE for token in FORBIDDEN_SWIFT_TOKENS):
        raise ValueError("CONTX app Swift source exceeds the approved host scope")
