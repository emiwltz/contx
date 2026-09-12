"""Auditable Swift source for the signed CONTX window host."""

from __future__ import annotations

from contx.macos_app.capture_native_source import CAPTURE_SWIFT_SOURCE
from contx.macos_app.permission_native_source import PERMISSION_SWIFT_SOURCE
from contx.macos_app.runtime_native_source import RUNTIME_VERIFY_SWIFT_SOURCE

CONTX_APP_SWIFT_SOURCE = r"""import AppKit
import ApplicationServices
import CoreGraphics
import CryptoKit
import Darwin
import Foundation
import Security

private let contractSchemaVersion = 2
private let controlSchemaVersion = 1
private let controlTimeoutSeconds: TimeInterval = 5.0
private let collectorStopTimeoutSeconds: TimeInterval = 10.0
private let maximumControlOutputBytes = 32 * 1024
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
}

private enum HostViewState {
    case starting
    case disabled
    case stopped
    case paused
    case active
    case error

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

private final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate {
    private let worker = DispatchQueue(label: "io.contx.desktop.runtime")
    private var contract: RuntimeContract?
    private let runtimeVerifier = RuntimeVerifier()
    private var integrityFailed = false
    private var collector: Process?
    private var shuttingDown = false
    private var refreshPending = false
    private var window: NSWindow?
    private var statusLine: NSTextField?
    private var pauseFifteenItem: NSButton?
    private var pauseIndefinitelyItem: NSButton?
    private var resumeItem: NSButton?
    private var restartItem: NSButton?
    private var timer: Timer?
    private var permissionButtons: [NSButton] = []
    private var permissionStatusLine: NSTextField?
    private var permissionBusy = false
    private var permissionSetupAvailable = false
    private var nativePermissionAccess: () -> PermissionAccess = PermissionAccess.native
    private var nativePermissionRequest: (PermissionAction) -> Void = { action in
        switch action {
        case .screenRecording: _ = CGRequestScreenCaptureAccess()
        case .accessibility:
            _ = AXIsProcessTrustedWithOptions(
                [kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true]
                as CFDictionary)
        case .verify: break
        }
    }
    private let commandLock = NSLock()
    private var boundedCommand: Process?
    private var commandsCancelled = false

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApplication.shared.setActivationPolicy(.regular)
        guard NSApplication.shared.activationPolicy() == .regular else {
            NSApplication.shared.terminate(nil)
            return
        }
        buildWindow()
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
        cancelBoundedCommand()
        worker.sync {
            stopCollector()
        }
    }

    func windowShouldClose(_ sender: NSWindow) -> Bool {
        NSApplication.shared.terminate(nil)
        return false
    }

    func applicationShouldHandleReopen(
        _ sender: NSApplication, hasVisibleWindows flag: Bool
    ) -> Bool {
        window?.makeKeyAndOrderFront(nil)
        return true
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

    private func buildWindow() {
        let window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 660, height: 580),
            styleMask: [.titled, .closable, .miniaturizable],
            backing: .buffered,
            defer: false
        )
        window.title = "CONTX"
        window.identifier = NSUserInterfaceItemIdentifier("io.contx.desktop.control")
        window.isReleasedWhenClosed = false
        window.delegate = self

        let heading = NSTextField(labelWithString: "CONTX")
        heading.font = .boldSystemFont(ofSize: 24)
        let status = NSTextField(labelWithString: "Collector starting")
        status.font = .systemFont(ofSize: 17, weight: .medium)
        status.setAccessibilityIdentifier("io.contx.desktop.state")
        let explanation = NSTextField(wrappingLabelWithString:
            "Closing this window quits CONTX and stops its collector.")
        explanation.textColor = .secondaryLabelColor

        let pauseFifteen = controlButton(
            title: "Pause for 15 minutes",
            action: #selector(pauseForFifteenMinutes(_:)))
        let pauseIndefinitely = controlButton(
            title: "Pause indefinitely", action: #selector(pauseIndefinitely(_:)))
        let resume = controlButton(
            title: "Resume collection", action: #selector(resumeCollection(_:)))
        let restart = controlButton(
            title: "Restart collector while paused",
            action: #selector(restartCollector(_:)))
        let quit = NSButton(title: "Quit CONTX", target: NSApplication.shared,
                            action: #selector(NSApplication.terminate(_:)))
        quit.bezelStyle = .rounded

        let permissionExplanation = NSTextField(wrappingLabelWithString:
            "Autorisations macOS : l’écran permet les captures ponctuelles ; "
            + "l’accessibilité permet les titres de fenêtres. Autoriser ne démarre "
            + "pas la collecte. Ces commandes nécessitent une collecte désactivée.")
        permissionExplanation.textColor = .secondaryLabelColor
        let screenPermission = controlButton(title: "Autoriser les captures…",
            action: #selector(requestScreenPermission(_:)))
        let accessibilityPermission = controlButton(title: "Autoriser les titres…",
            action: #selector(requestAccessibilityPermission(_:)))
        let verifyPermission = controlButton(title: "Vérifier les autorisations",
            action: #selector(verifyPermissions(_:)))
        let permissionRow = NSStackView(
            views: [screenPermission, accessibilityPermission])
        permissionRow.spacing = 8
        let permissionStatus = NSTextField(wrappingLabelWithString:
            "Autorisations non vérifiées. "
            + "Aucun contenu n’est lu pendant la vérification.")
        permissionStatus.setAccessibilityIdentifier("io.contx.desktop.permissions")
        permissionStatusLine = permissionStatus
        let testCapture = controlButton(title: "Tester une capture fictive",
            action: #selector(testSyntheticCapture(_:)))
        permissionButtons = [
            screenPermission, accessibilityPermission, verifyPermission, testCapture]

        let firstRow = NSStackView(views: [pauseFifteen, pauseIndefinitely])
        let secondRow = NSStackView(views: [resume, restart])
        firstRow.spacing = 8
        secondRow.spacing = 8
        let stack = NSStackView(views: [
            heading, status, explanation, firstRow, secondRow,
            permissionExplanation, permissionRow, verifyPermission,
            permissionStatus, testCapture, quit,
        ])
        stack.orientation = .vertical
        stack.alignment = .leading
        stack.spacing = 16
        stack.translatesAutoresizingMaskIntoConstraints = false
        let content = window.contentView!
        content.addSubview(stack)
        NSLayoutConstraint.activate([
            stack.leadingAnchor.constraint(
                equalTo: content.leadingAnchor, constant: 24),
            stack.trailingAnchor.constraint(
                equalTo: content.trailingAnchor, constant: -24),
            stack.topAnchor.constraint(equalTo: content.topAnchor, constant: 24),
            stack.bottomAnchor.constraint(
                lessThanOrEqualTo: content.bottomAnchor, constant: -24),
        ])

        let mainMenu = NSMenu()
        let appMenuItem = NSMenuItem()
        let appMenu = NSMenu()
        let quitMenuItem = NSMenuItem(title: "Quit CONTX",
            action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        quitMenuItem.target = NSApplication.shared
        appMenu.addItem(quitMenuItem)
        appMenuItem.submenu = appMenu
        mainMenu.addItem(appMenuItem)
        NSApplication.shared.mainMenu = mainMenu

        self.window = window
        statusLine = status
        pauseFifteenItem = pauseFifteen
        pauseIndefinitelyItem = pauseIndefinitely
        resumeItem = resume
        restartItem = restart
        window.center()
        window.makeKeyAndOrderFront(nil)
        NSApplication.shared.activate()
    }

    private func controlButton(title: String, action: Selector) -> NSButton {
        let button = NSButton(title: title, target: self, action: action)
        button.bezelStyle = .rounded
        button.isEnabled = false
        return button
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
        guard !refreshPending, !integrityFailed else {
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
                try self.verifyRuntime(hashAll: true)
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
        guard contract != nil else {
            throw HostFailure.invalidContract
        }
        let result = try runBoundedCommand(
            module: "contx.macos_app.control",
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
        try verifyRuntime(hashAll: true)
        let process = Process()
        process.executableURL = URL(
            fileURLWithPath: contract.root + "/" + contract.python)
        process.arguments = ["-I", "-B", "-m", "contx.daemon.entrypoint"]
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
        module: String,
        arguments: [String],
        nativeHostChild: Bool,
        timeout: TimeInterval = controlTimeoutSeconds
    ) throws -> CommandResult {
        try verifyRuntime()
        let process = Process()
        let standardOutput = Pipe()
        let standardError = Pipe()
        guard let contract else { throw HostFailure.invalidContract }
        process.executableURL = URL(
            fileURLWithPath: contract.root + "/" + contract.python)
        process.arguments = ["-I", "-B", "-m", module] + arguments
        process.environment = minimalEnvironment(nativeHostChild: nativeHostChild)
        process.standardOutput = standardOutput
        process.standardError = standardError
        commandLock.lock()
        guard !commandsCancelled else {
            commandLock.unlock()
            throw HostFailure.commandFailed
        }
        do {
            try process.run()
            boundedCommand = process
            commandLock.unlock()
        } catch {
            commandLock.unlock()
            throw HostFailure.commandFailed
        }
        defer {
            commandLock.lock()
            if boundedCommand === process { boundedCommand = nil }
            commandLock.unlock()
        }
        guard waitForExit(process, timeout: timeout) else {
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

    private func cancelBoundedCommand() {
        commandLock.lock()
        commandsCancelled = true
        if let process = boundedCommand, process.isRunning { process.terminate() }
        commandLock.unlock()
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
        if let contract {
            environment["CONTX_OPTMEM_EXECUTABLE"] =
                contract.root + "/" + contract.optmem
        }
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
        statusLine?.stringValue = viewState.statusText
        permissionSetupAvailable = status?.state == .disabled
            && status?.backgroundEnabled == false && status?.collectorRunning == false
        updatePermissionButtons()
        let unavailable = "CONTX est indisponible. Aucun accès n’est confirmé."
        if viewState == .error {
            permissionStatusLine?.stringValue = unavailable
        } else if status != nil && permissionStatusLine?.stringValue == unavailable {
            permissionStatusLine?.stringValue =
                "Autorisations à vérifier. Aucun accès n’est confirmé."
        }

        let enabled = status?.backgroundEnabled == true
        let running = status?.collectorRunning == true
        let paused = status?.collectionPaused == true
        pauseFifteenItem?.isEnabled = enabled && running && !paused
        pauseIndefinitelyItem?.isEnabled = enabled && running && !paused
        resumeItem?.isEnabled = enabled && running && paused
        restartItem?.isEnabled = enabled && !running && paused
    }

    private func loadRuntimeContract() throws -> RuntimeContract {
        // Resource seals are not a substitute for checking them before trusting
        // the embedded manifest. Validate this exact bundle before reading it.
        var signedCode: SecStaticCode?
        guard SecStaticCodeCreateWithPath(
            Bundle.main.bundleURL as CFURL, SecCSFlags(), &signedCode
        ) == errSecSuccess, let signedCode,
        SecStaticCodeCheckValidity(signedCode,
            SecCSFlags(rawValue: kSecCSStrictValidate), nil) == errSecSuccess else {
            throw HostFailure.invalidBundle
        }
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
        try runtimeVerifier.verify(contract, hashAll: true)
        return contract
    }

    private func verifyRuntime(hashAll: Bool = false) throws {
        guard let contract else { throw HostFailure.invalidContract }
        do {
            try runtimeVerifier.verify(contract, hashAll: hashAll)
        } catch {
            stopCollector()
            DispatchQueue.main.async { [weak self] in
                self?.integrityFailed = true
                self?.apply(viewState: .error, status: nil)
            }
            throw error
        }
    }

    static func processOnce() -> Int32 {
        let host = AppDelegate()
        do {
            host.contract = try host.loadRuntimeContract()
            guard let contract = host.contract else {
                throw HostFailure.invalidContract
            }
            // Replace the verified native launcher with the exact private interpreter.
            // launchd owns this PID and delivers shutdown signals directly.
            let arguments = [contract.root + "/" + contract.python,
                             "-I", "-B", "-m", "contx.processing.entrypoint"]
            let environment = host.minimalEnvironment(nativeHostChild: false)
                .map { "\($0.key)=\($0.value)" }
            let argv = arguments.map { strdup($0) } + [nil]
            let envp = environment.map { strdup($0) } + [nil]
            defer { argv.forEach { free($0) }; envp.forEach { free($0) } }
            argv.withUnsafeBufferPointer { args in
                envp.withUnsafeBufferPointer { env in
                    _ = execve(arguments[0], args.baseAddress!, env.baseAddress!)
                }
            }
            throw HostFailure.commandFailed
        } catch {
            fputs("CONTX processor refused an invalid runtime or launch.\n", stderr)
            return 2
        }
    }

}

if CommandLine.arguments.count == 2 && CommandLine.arguments[1] == "--process-once" {
    exit(AppDelegate.processOnce())
}
guard CommandLine.arguments.count == 1 else { exit(64) }
let application = NSApplication.shared
private let delegate = AppDelegate()
application.delegate = delegate
application.setActivationPolicy(.regular)
application.run()
"""

CONTX_APP_SWIFT_SOURCE = CONTX_APP_SWIFT_SOURCE.replace(
    "private enum ControlState",
    RUNTIME_VERIFY_SWIFT_SOURCE
    + PERMISSION_SWIFT_SOURCE
    + CAPTURE_SWIFT_SOURCE
    + "\nprivate enum ControlState",
)

ALLOWED_SWIFT_IMPORTS = frozenset(
    {
        "AppKit",
        "ApplicationServices",
        "CoreGraphics",
        "CryptoKit",
        "Darwin",
        "Foundation",
        "Security",
    }
)
FORBIDDEN_SWIFT_TOKENS = (
    "AXUIElement",
    "CGWindowList",
    "NSWorkspace",
    "NWConnection",
    "ScreenCaptureKit",
    "URLSession",
)


def validate_native_source() -> None:
    """Reject capture, observation, or network scope beyond permission setup."""
    imports = {
        line.removeprefix("import ").strip()
        for line in CONTX_APP_SWIFT_SOURCE.splitlines()
        if line.startswith("import ")
    }
    if imports != ALLOWED_SWIFT_IMPORTS:
        raise ValueError("CONTX app Swift imports exceed the approved host scope")
    if any(token in CONTX_APP_SWIFT_SOURCE for token in FORBIDDEN_SWIFT_TOKENS):
        raise ValueError("CONTX app Swift source exceeds the approved host scope")
