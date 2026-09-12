"""Audit Mach-O load commands against private runtime and macOS library roots."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

SYSTEM_PREFIXES = ("/System/Library/", "/usr/lib/")
MACHO_MAGICS = {
    b"\xcf\xfa\xed\xfe",
    b"\xfe\xed\xfa\xcf",
    b"\xce\xfa\xed\xfe",
    b"\xfe\xed\xfa\xce",
    b"\xca\xfe\xba\xbe",
    b"\xbe\xba\xfe\xca",
    b"\xca\xfe\xba\xbf",
    b"\xbf\xba\xfe\xca",
}


def load_commands(text: str) -> tuple[list[str], list[str]]:
    dependencies: list[str] = []
    rpaths: list[str] = []
    for block in text.split("Load command "):
        lines = [line.strip() for line in block.splitlines()]
        commands = [line[4:] for line in lines if line.startswith("cmd ")]
        if not commands:
            continue
        command = commands[0]
        if command in {
            "LC_LOAD_DYLIB",
            "LC_LOAD_WEAK_DYLIB",
            "LC_REEXPORT_DYLIB",
            "LC_LOAD_UPWARD_DYLIB",
            "LC_LAZY_LOAD_DYLIB",
        }:
            dependencies.extend(
                line[5:].split(" (offset", 1)[0]
                for line in lines
                if line.startswith("name ")
            )
        elif command == "LC_RPATH":
            rpaths.extend(
                line[5:].split(" (offset", 1)[0]
                for line in lines
                if line.startswith("path ")
            )
    return dependencies, rpaths


def _inspect(path: Path) -> str:
    return subprocess.check_output(
        ["/usr/bin/otool", "-l", str(path)], text=True, timeout=15
    )


def audit_native_dependencies(
    root: Path,
    *,
    inspect: Callable[[Path], str] = _inspect,
) -> int:
    executable_directory = root / "python/bin"
    main_rpaths = load_commands(inspect(executable_directory / "python3.12"))[1]
    count = 0

    def expand(value: str, loader: Path) -> Path:
        if value.startswith("@loader_path/"):
            return (loader / value.removeprefix("@loader_path/")).resolve()
        if value.startswith("@executable_path/"):
            return (
                executable_directory / value.removeprefix("@executable_path/")
            ).resolve()
        if value.startswith("/"):
            return Path(value).resolve()
        raise ValueError(f"Unsupported native library reference: {value}")

    for path in root.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        with path.open("rb") as stream:
            if stream.read(4) not in MACHO_MAGICS:
                continue
        count += 1
        dependencies, rpaths = load_commands(inspect(path))
        search = [expand(value, path.parent) for value in rpaths]
        search += [expand(value, executable_directory) for value in main_rpaths]
        if any(not directory.is_relative_to(root) for directory in search):
            raise ValueError("Native library search path escapes the private runtime")
        for dependency in dependencies:
            if dependency.startswith(SYSTEM_PREFIXES):
                continue
            if dependency.startswith("@rpath/"):
                candidates = [
                    directory / dependency.removeprefix("@rpath/")
                    for directory in search
                ]
            else:
                candidates = [expand(dependency, path.parent)]
            if not any(
                candidate.is_file() and candidate.resolve().is_relative_to(root)
                for candidate in candidates
            ):
                raise ValueError(
                    f"Unresolved or external native dependency: {dependency}"
                )
    return count
