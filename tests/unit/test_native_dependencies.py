"""Reject native libraries outside the private release before signing it."""

from pathlib import Path

import pytest

from contx.macos_app.native_dependencies import audit_native_dependencies, load_commands


def command(kind: str, value: str) -> str:
    field = "path" if kind == "LC_RPATH" else "name"
    return f"Load command 0\n cmd {kind}\n {field} {value} (offset 24)\n"


def test_library_identity_is_not_an_external_load_dependency():
    text = command("LC_ID_DYLIB", "/old/build/path/libpython.dylib")
    text += command("LC_LOAD_DYLIB", "/usr/lib/libSystem.B.dylib")
    assert load_commands(text) == (["/usr/lib/libSystem.B.dylib"], [])


@pytest.mark.parametrize("dependency", ["@rpath/private.dylib", "/outside/lib.dylib"])
def test_audits_actual_load_commands(tmp_path: Path, dependency: str):
    root = tmp_path / "runtime"
    binary = root / "python/bin/python3.12"
    binary.parent.mkdir(parents=True)
    library = root / "python/lib/private.dylib"
    library.parent.mkdir()
    binary.write_bytes(b"\xcf\xfa\xed\xfe")
    library.write_bytes(b"\xcf\xfa\xed\xfe")

    def inspect(path):
        if path == binary:
            return command("LC_RPATH", "@executable_path/../lib") + command(
                "LC_LOAD_DYLIB", dependency
            )
        return command("LC_ID_DYLIB", "/old/build/path/private.dylib")

    if dependency.startswith("@"):
        assert audit_native_dependencies(root, inspect=inspect) == 2
    else:
        with pytest.raises(ValueError, match="external"):
            audit_native_dependencies(root, inspect=inspect)
