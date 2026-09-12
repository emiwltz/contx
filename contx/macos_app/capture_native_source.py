"""Explicit bounded synthetic capture dispatch and in-memory result preview."""

CAPTURE_SWIFT_SOURCE = r"""
private struct SyntheticCaptureResult: Decodable {
    let schema_version: Int
    let output_path: String
    let width: Int
    let height: Int
    let content_hash: String
    let focus_race_reason: String
}

private struct SyntheticCaptureFailure: Decodable {
    let schema_version: Int
    let failure_site: String
}

private enum SyntheticCaptureError: Error {
    case child(String)
}

extension AppDelegate {
    @objc private func testSyntheticCapture(_ sender: Any?) {
        guard permissionSetupAvailable, !permissionBusy, !integrityFailed,
              !shuttingDown else { return }
        permissionBusy = true
        updatePermissionButtons()
        permissionStatusLine?.stringValue =
            "Test en cours : une fenêtre fictive va apparaître. "
            + "Laissez-la au premier plan. Capture limitée à 40 secondes."
        worker.async { [weak self] in
            guard let self else { return }
            var stage = "vérification de CONTX"
            do {
                try self.verifyRuntime(hashAll: true)
                stage = "vérification de l’arrêt de la collecte"
                try Self.requireDisabledSetup(self.readControlStatus())
                guard self.collector == nil else { throw HostFailure.commandFailed }
                let directory = FileManager.default.temporaryDirectory
                    .resolvingSymlinksInPath()
                    .appendingPathComponent("contx-capture-" + UUID().uuidString,
                                            isDirectory: true)
                try FileManager.default.createDirectory(at: directory,
                    withIntermediateDirectories: false,
                    attributes: [.posixPermissions: 0o700])
                let image: NSImage
                do {
                    stage = "exécution du test"
                    let target = directory.appendingPathComponent("synthetic.png")
                    let command = try self.runBoundedCommand(
                        module: "contx.daemon.entrypoint",
                        arguments: ["--synthetic-capture", target.path],
                        nativeHostChild: true, timeout: 40.0)
                    if command.status != 0,
                       let failure = try? JSONDecoder().decode(
                        SyntheticCaptureFailure.self, from: command.stdout),
                       failure.schema_version == 1,
                       failure.failure_site.range(
                        of: "^[a-z_]+:[0-9]+$", options: .regularExpression) != nil {
                        throw SyntheticCaptureError.child(failure.failure_site)
                    }
                    stage = "validation du résultat"
                    guard command.status == 0,
                          let result = try? JSONDecoder().decode(
                            SyntheticCaptureResult.self, from: command.stdout),
                          result.schema_version == 1,
                          result.output_path == target.path,
                          result.focus_race_reason == "focused_window_changed",
                          result.width > 0, result.width <= 8192,
                          result.height > 0, result.height <= 8192 else {
                        throw HostFailure.invalidControlResponse
                    }
                    let attributes = try FileManager.default.attributesOfItem(
                        atPath: target.path)
                    guard attributes[.type] as? FileAttributeType == .typeRegular,
                          let size = attributes[.size] as? NSNumber,
                          size.intValue <= 10 * 1024 * 1024 else {
                        throw HostFailure.invalidControlResponse
                    }
                    let data = try Data(contentsOf: target)
                    stage = "chargement de l’image"
                    let digest = SHA256.hash(data: data)
                        .map { String(format: "%02x", $0) }.joined()
                    guard digest == result.content_hash,
                          let decoded = NSImage(data: data) else {
                        throw HostFailure.invalidControlResponse
                    }
                    image = decoded
                    try FileManager.default.removeItem(at: directory)
                } catch {
                    do { try FileManager.default.removeItem(at: directory) }
                    catch {
                        self.finishPermissionAction(
                            "Test arrêté. Le nettoyage du fichier temporaire a échoué.")
                        return
                    }
                    throw error
                }
                DispatchQueue.main.async { [weak self] in
                    guard let self, !self.shuttingDown else { return }
                    self.finishPermissionAction(
                        "Capture fictive réussie ; changement de fenêtre refusé. "
                        + "Image temporaire supprimée. La collecte reste désactivée.")
                    let preview = NSImageView(frame: NSRect(x: 0, y: 0,
                                                            width: 480, height: 321))
                    preview.image = image
                    preview.imageScaling = .scaleProportionallyUpOrDown
                    let alert = NSAlert()
                    alert.messageText = "Capture fictive réussie"
                    alert.informativeText =
                        "Seule la fenêtre bleue et son texte fictif sont attendus. "
                        + "Le fichier temporaire a été supprimé."
                    alert.accessoryView = preview
                    alert.addButton(withTitle: "Fermer le résultat")
                    NSApplication.shared.activate(ignoringOtherApps: true)
                    if let window = self.window { alert.beginSheetModal(for: window) }
                }
            } catch {
                if case SyntheticCaptureError.child(let site) = error {
                    stage = "test interne — " + site
                }
                self.finishPermissionAction(
                    "Capture fictive impossible ou interrompue (\(stage)). "
                    + "Vérifiez les autorisations et laissez la fenêtre fictive "
                    + "au premier plan. "
                    + "La collecte reste désactivée.")
            }
        }
    }
}
"""
