"""Explicit native permission actions and content-free collector verification."""

PERMISSION_SWIFT_SOURCE = r"""
private enum PermissionAction {
    case screenRecording
    case accessibility
    case verify
}

private struct PermissionAccess: Decodable {
    let schemaVersion: Int
    let screenRecording: Bool
    let accessibility: Bool
    let permissionsRequested: Bool
    let contentRead: Bool

    private enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case screenRecording = "screen_recording"
        case accessibility
        case permissionsRequested = "permissions_requested"
        case contentRead = "content_read"
    }

    static func decode(_ result: CommandResult) throws -> PermissionAccess {
        guard result.status == 0,
              result.stdout.count <= maximumControlOutputBytes,
              let access = try? JSONDecoder().decode(PermissionAccess.self,
                                                    from: result.stdout),
              access.schemaVersion == 1,
              !access.permissionsRequested, !access.contentRead else {
            throw HostFailure.invalidControlResponse
        }
        return access
    }

    static func native() -> PermissionAccess {
        PermissionAccess(schemaVersion: 1,
                         screenRecording: CGPreflightScreenCaptureAccess(),
                         accessibility: AXIsProcessTrusted(),
                         permissionsRequested: false, contentRead: false)
    }

    var summary: String {
        let screen = screenRecording ? "autorisé" : "non autorisé"
        let titles = accessibility ? "autorisée" : "non autorisée"
        return "Écran : \(screen) ; accessibilité : \(titles)."
    }
}

extension AppDelegate {
    @objc private func requestScreenPermission(_ sender: Any?) {
        beginPermissionAction(.screenRecording)
    }

    @objc private func requestAccessibilityPermission(_ sender: Any?) {
        beginPermissionAction(.accessibility)
    }

    @objc private func verifyPermissions(_ sender: Any?) {
        beginPermissionAction(.verify)
    }

    private static func requireDisabledSetup(_ status: ControlStatus) throws {
        guard status.state == .disabled, !status.backgroundEnabled,
              status.collectionPaused, !status.collectorRunning else {
            throw HostFailure.commandFailed
        }
    }

    private func updatePermissionButtons() {
        let enabled = permissionSetupAvailable && !permissionBusy
            && !integrityFailed
            && !shuttingDown
        permissionButtons.forEach { $0.isEnabled = enabled }
    }

    private func beginPermissionAction(_ action: PermissionAction) {
        guard permissionSetupAvailable, !permissionBusy, !integrityFailed,
              !shuttingDown else { return }
        permissionBusy = true
        updatePermissionButtons()
        permissionStatusLine?.stringValue = "Vérification de CONTX…"
        worker.async { [weak self] in
            guard let self else { return }
            do {
                try self.verifyRuntime(hashAll: true)
                try Self.requireDisabledSetup(self.readControlStatus())
                guard self.collector == nil else { throw HostFailure.commandFailed }
                if action == .verify {
                    let native = self.nativePermissionAccess()
                    let result = try self.runBoundedCommand(
                        module: "contx.daemon.entrypoint",
                        arguments: ["--permission-preflight"], nativeHostChild: true)
                    let child = try PermissionAccess.decode(result)
                    self.finishPermissionAction(
                        "Application — \(native.summary)\n"
                        + "Collecteur — \(child.summary)\n"
                        + "Vérification sans lecture de contenu. "
                        + "La collecte reste désactivée.")
                } else {
                    DispatchQueue.main.async { [weak self] in
                        guard let self, !self.shuttingDown else { return }
                        // Requests are made only here, after an explicit button action.
                        self.nativePermissionRequest(action)
                        self.finishPermissionAction(
                            "Demande transmise à macOS. Après votre choix, cliquez sur "
                            + "Vérifier les autorisations. "
                            + "La collecte reste désactivée.")
                    }
                }
            } catch {
                self.finishPermissionAction(
                    "Vérification impossible. La collecte doit être désactivée "
                    + "et CONTX "
                    + "doit être intact. Aucun accès n’est confirmé.")
            }
        }
    }

    private func finishPermissionAction(_ message: String) {
        DispatchQueue.main.async { [weak self] in
            guard let self, !self.shuttingDown else { return }
            self.permissionBusy = false
            self.permissionStatusLine?.stringValue = message
            self.updatePermissionButtons()
        }
    }
}
"""
