"""Exercise the Swift verifier without windows or Python children."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path

from contx.macos_app.native_source import CONTX_APP_SWIFT_SOURCE
from contx.macos_app.runtime_release import inventory, remove_generated_tree, seal_tree

PROBE = r"""
private let contract = try JSONDecoder().decode(RuntimeContract.self,
    from: Data(contentsOf: URL(fileURLWithPath: CommandLine.arguments[1])))
private let verifier = RuntimeVerifier()
let start = Date()
do { try verifier.verify(contract, hashAll: true) }
catch { fputs("Verifier refused: \(error)\n", stderr); exit(2) }
let full = Date().timeIntervalSince(start)
let polling = Date()
for _ in 0..<10 { try verifier.verify(contract) }
print("full=\(full) seconds; poll=\(Date().timeIntervalSince(polling) / 10) seconds")
if CommandLine.arguments.count > 2 {
    print("READY")
    fflush(stdout)
    _ = readLine()
    do {
        try verifier.verify(contract)
        exit(21)
    } catch { print("REJECTED"); fflush(stdout) }
    _ = readLine()
    do { try verifier.verify(contract); exit(22) }
    catch { print("LATCHED") }
}
"""


def compile_probe(root: Path) -> Path:
    # Exclude all real application dispatch, including the processor entrypoint.
    source = CONTX_APP_SWIFT_SOURCE.split("\nif CommandLine.arguments.count == 2", 1)[0]
    swift = root / "Verifier.swift"
    executable = root / "verifier"
    swift.write_text(source + PROBE)
    subprocess.run(
        [
            "xcrun",
            "swiftc",
            str(swift),
            "-framework",
            "AppKit",
            "-framework",
            "CryptoKit",
            "-framework",
            "Foundation",
            "-module-cache-path",
            str(root / "modules"),
            "-o",
            str(executable),
        ],
        check=True,
        timeout=120,
    )
    return executable


def fixture(root: Path) -> tuple[Path, Path]:
    release = root / "release"
    (release / "env/bin").mkdir(parents=True)
    (release / "optmem").mkdir()
    for name in ["env/bin/python3", "optmem/memo", "module.py", "native.dylib"]:
        target = release / name
        target.write_bytes(b"synthetic-runtime-file")
        target.chmod(0o700)
    seal_tree(release)
    manifest = root / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schemaVersion": 2,
                "root": str(release),
                "sourceCommit": "a" * 40,
                "lockSHA256": "b" * 64,
                "pythonVersion": "synthetic",
                "python": "env/bin/python3",
                "optmem": "optmem/memo",
                "files": [record.model_dump() for record in inventory(release)],
            }
        )
    )
    return release, manifest


def synthetic_cases(executable: Path, root: Path) -> None:
    cases = [
        "env/bin/python3",
        "module.py",
        "native.dylib",
        "optmem/memo",
        "extra",
        "missing",
        "external-link",
        "writable",
    ]
    for index, case in enumerate(cases):
        parent = root / f"case-{index}"
        parent.mkdir()
        release, manifest = fixture(parent)
        process = subprocess.Popen(
            [str(executable), str(manifest), "mutate"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        assert process.stdout is not None and process.stdin is not None
        try:
            print(case, process.stdout.readline().strip())
            ready = process.stdout.readline().strip()
            if ready != "READY":
                raise RuntimeError(process.stderr.read() if process.stderr else ready)
            target = release / (case if case in cases[:4] else "module.py")
            previous = target.stat()
            release.chmod(0o700)
            if case == "extra":
                (release / "extra.py").write_text("extra")
                (release / "extra.py").chmod(0o400)
            elif case == "missing":
                target.unlink()
            elif case == "external-link":
                target.unlink()
                target.symlink_to("/usr/bin/true")
            else:
                target.chmod(0o700)
                if case != "writable":
                    target.write_bytes(b"altered-runtime-file!!")
                    target.chmod(0o500)
                    os.utime(target, ns=(previous.st_atime_ns, previous.st_mtime_ns))
            release.chmod(0o500)
            process.stdin.write("check\n")
            process.stdin.flush()
            assert process.stdout.readline().strip() == "REJECTED"
            # Even after disk repair the running verifier must stay failed closed.
            remove_generated_tree(release)
            fixture(parent)
            process.stdin.write("check again\n")
            process.stdin.flush()
            stdout, stderr = process.communicate(timeout=15)
            assert process.returncode == 0 and "LATCHED" in stdout, stderr
            # A fresh verifier must reject altered bytes even without a prior stamp.
            target = release / "module.py"
            target.chmod(0o700)
            target.write_bytes(b"startup corruption")
            target.chmod(0o500)
            result = subprocess.run(
                [str(executable), str(manifest)], capture_output=True, timeout=15
            )
            assert result.returncode != 0
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            remove_generated_tree(release)
    print(
        "Swift runtime verifier: all startup, polling and latched failure cases passed"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="contx-native-runtime-") as temporary:
        root = Path(temporary).resolve()
        executable = compile_probe(root)
        if args.manifest:
            subprocess.run(
                [str(executable), str(args.manifest)], check=True, timeout=60
            )
        else:
            synthetic_cases(executable, root)


if __name__ == "__main__":
    main()
