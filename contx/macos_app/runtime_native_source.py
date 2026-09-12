"""Native verification before any code from the private Python release executes."""

RUNTIME_VERIFY_SWIFT_SOURCE = r"""
private struct RuntimeFile: Decodable {
    let path: String
    let kind: String
    let mode: UInt32
    let sha256: String
    let target: String
}

private struct RuntimeContract: Decodable {
    let schemaVersion: Int
    let root: String
    let sourceCommit: String
    let lockSHA256: String
    let pythonVersion: String
    let python: String
    let optmem: String
    let files: [RuntimeFile]
}

private struct RuntimeStamp: Equatable {
    let device: Int32
    let inode: UInt64
    let size: Int64
    let mode: UInt16
    let modifiedSeconds: Int
    let modifiedNanos: Int
    let changedSeconds: Int
    let changedNanos: Int

    init(_ info: stat) {
        device = info.st_dev
        inode = info.st_ino
        size = info.st_size
        mode = info.st_mode
        modifiedSeconds = info.st_mtimespec.tv_sec
        modifiedNanos = info.st_mtimespec.tv_nsec
        changedSeconds = info.st_ctimespec.tv_sec
        changedNanos = info.st_ctimespec.tv_nsec
    }
}

private func canonicalRuntimePath(_ path: String) -> String? {
    guard let resolved = realpath(path, nil) else { return nil }
    defer { free(resolved) }
    return String(cString: resolved)
}

private final class RuntimeVerifier {
    private var baseline: [String: RuntimeStamp]?
    private var failed = false

    func verify(_ contract: RuntimeContract, hashAll: Bool = false) throws {
        guard !failed else { throw HostFailure.changedExecutable }
        do {
            try check(contract, hashAll: hashAll)
        } catch {
            // Never repair the trust baseline in a running process after mutation.
            failed = true
            baseline = nil
            throw error
        }
    }

    private func check(_ contract: RuntimeContract, hashAll: Bool) throws {
        let root = contract.root
        guard contract.schemaVersion == 2, root.hasPrefix("/"),
              canonicalRuntimePath(root) == root,
              contract.python == "env/bin/python3", contract.optmem == "optmem/memo",
              contract.files.count > 0, contract.files.count < 100000 else {
            throw HostFailure.invalidContract
        }
        if let baseline, !hashAll {
            // Directory ctime/mtime also detect new or removed names. Do not rebuild
            // the manifest dictionary or re-enumerate unchanged directories each tick.
            for (relative, previous) in baseline {
                let path = relative == "." ? root : root + "/" + relative
                var info = stat()
                guard lstat(path, &info) == 0, info.st_uid == getuid(),
                      RuntimeStamp(info) == previous else {
                    throw HostFailure.changedExecutable
                }
            }
            return
        }
        var expected: [String: RuntimeFile] = [:]
        for record in contract.files {
            let components = record.path.split(
                separator: "/", omittingEmptySubsequences: false)
            guard record.path == "." || (!record.path.hasPrefix("/") &&
                  components.allSatisfy({ !$0.isEmpty && $0 != "." && $0 != ".." })),
                  expected[record.path] == nil else {
                throw HostFailure.invalidContract
            }
            expected[record.path] = record
        }
        var stamps: [String: RuntimeStamp] = [:]
        let hashBytes = baseline == nil || hashAll
        func visit(_ relative: String) throws {
            let path = relative == "." ? root : root + "/" + relative
            guard let record = expected[relative] else {
                throw HostFailure.changedExecutable
            }
            var info = stat()
            guard lstat(path, &info) == 0, info.st_uid == getuid(),
                  UInt32(info.st_mode & 0o7777) == record.mode else {
                throw HostFailure.changedExecutable
            }
            let stamp = RuntimeStamp(info)
            if let baseline, baseline[relative] != stamp {
                throw HostFailure.changedExecutable
            }
            stamps[relative] = stamp
            let type = info.st_mode & S_IFMT
            switch record.kind {
            case "directory":
                guard type == S_IFDIR, record.mode == 0o500 else {
                    throw HostFailure.changedExecutable
                }
                for name in try FileManager.default.contentsOfDirectory(atPath: path) {
                    try visit(relative == "." ? name : relative + "/" + name)
                }
            case "symlink":
                guard type == S_IFLNK,
                      try FileManager.default.destinationOfSymbolicLink(atPath: path)
                        == record.target else { throw HostFailure.changedExecutable }
                guard let target = canonicalRuntimePath(path),
                      target.hasPrefix(root + "/"),
                      FileManager.default.fileExists(atPath: target) else {
                    throw HostFailure.changedExecutable
                }
            case "file":
                guard type == S_IFREG,
                      record.mode == 0o400 || record.mode == 0o500 else {
                    throw HostFailure.changedExecutable
                }
                if hashBytes {
                    let data = try Data(contentsOf: URL(fileURLWithPath: path))
                    let digest = SHA256.hash(data: data)
                        .map { String(format: "%02x", $0) }.joined()
                    guard digest == record.sha256 else {
                        throw HostFailure.changedExecutable
                    }
                }
            default: throw HostFailure.invalidContract
            }
        }
        try visit(".")
        guard stamps.count == expected.count,
              expected[contract.python] != nil,
              expected[contract.optmem]?.kind == "file",
              FileManager.default.isExecutableFile(atPath: root + "/" + contract.python)
        else { throw HostFailure.invalidContract }
        baseline = stamps
    }
}
"""
