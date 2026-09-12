"""Private, versioned runtime construction and complete inventory verification."""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from contx.memory_store.optmem import OPTMEM_SNAPSHOT_SHA256


class RuntimeReleaseError(ValueError):
    """A release cannot be trusted or completely constructed."""


class RuntimeFile(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    path: str
    kind: Literal["file", "directory", "symlink"]
    mode: int
    sha256: str = ""
    target: str = ""


class RuntimeManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schemaVersion: Literal[2] = 2
    root: str
    sourceCommit: str = Field(pattern=r"^[0-9a-f]{40}$")
    lockSHA256: str = Field(pattern=r"^[0-9a-f]{64}$")
    pythonVersion: str
    python: Literal["env/bin/python3"] = "env/bin/python3"
    optmem: Literal["optmem/memo"] = "optmem/memo"
    files: list[RuntimeFile]


def file_digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _canonical_directory(path: Path) -> None:
    if not path.is_absolute() or path.resolve(strict=True) != path:
        raise RuntimeReleaseError("Runtime path must be absolute and canonical")
    if not path.is_dir() or path.stat().st_uid != os.getuid():
        raise RuntimeReleaseError("Runtime directory must belong to this user")


def inventory(root: Path) -> list[RuntimeFile]:
    """Inventory all nodes, refusing external links and writable code."""
    _canonical_directory(root)
    records = []
    for path in [root, *sorted(root.rglob("*"))]:
        info = path.lstat()
        relative = "." if path == root else path.relative_to(root).as_posix()
        mode = stat.S_IMODE(info.st_mode)
        if info.st_uid != os.getuid():
            raise RuntimeReleaseError(f"Foreign runtime owner: {relative}")
        if path.is_symlink():
            target = path.resolve(strict=True)
            if not target.is_relative_to(root):
                raise RuntimeReleaseError(f"External runtime symlink: {relative}")
            records.append(
                RuntimeFile(
                    path=relative, kind="symlink", mode=mode, target=os.readlink(path)
                )
            )
            continue
        if mode not in (0o400, 0o500):
            raise RuntimeReleaseError(f"Runtime node is not sealed: {relative}")
        if stat.S_ISDIR(info.st_mode):
            records.append(RuntimeFile(path=relative, kind="directory", mode=mode))
        elif stat.S_ISREG(info.st_mode):
            records.append(
                RuntimeFile(
                    path=relative, kind="file", mode=mode, sha256=file_digest(path)
                )
            )
        else:
            raise RuntimeReleaseError(f"Unsupported runtime node: {relative}")
    return records


def verify_manifest(manifest: RuntimeManifest) -> None:
    root = Path(manifest.root)
    expected = manifest.files
    if inventory(root) != expected:
        raise RuntimeReleaseError("Runtime inventory differs from sealed manifest")
    if len({entry.path for entry in expected}) != len(expected):
        raise RuntimeReleaseError("Duplicate runtime path")
    for relative in (manifest.python, manifest.optmem):
        path = root / relative
        if not path.is_file() or not os.access(path, os.X_OK):
            raise RuntimeReleaseError(f"Missing runtime command: {relative}")
    if file_digest(root / manifest.optmem) != OPTMEM_SNAPSHOT_SHA256:
        raise RuntimeReleaseError("OptMem is not the reviewed snapshot")


def load_manifest(path: Path) -> RuntimeManifest:
    manifest = RuntimeManifest.model_validate_json(path.read_bytes())
    verify_manifest(manifest)
    return manifest


def seal_tree(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        if not path.is_symlink():
            path.chmod(0o500 if path.is_dir() or path.stat().st_mode & 0o111 else 0o400)
    root.chmod(0o500)


def remove_generated_tree(root: Path) -> None:
    """Only call for an exclusively created, unpublished build directory."""
    root.chmod(0o700)
    for path in root.rglob("*"):
        if path.is_dir() and not path.is_symlink():
            path.chmod(0o700)
    shutil.rmtree(root)


def _run(command: Sequence[str]) -> None:
    subprocess.run(command, check=True, timeout=300, stdout=subprocess.DEVNULL)


def build_private_runtime(
    root: Path,
    manifest_path: Path,
    *,
    repository: Path,
    python_home: Path,
    optmem: Path,
    uv: Path,
    run: Callable[[Sequence[str]], None] = _run,
) -> RuntimeManifest:
    """Build a fresh final-path release; never select, install or activate it."""
    _canonical_directory(root.parent)
    _canonical_directory(manifest_path.parent)
    if (
        root.exists()
        or root.is_symlink()
        or manifest_path.exists()
        or manifest_path.is_symlink()
    ):
        raise RuntimeReleaseError("Release and manifest destinations must be new")
    if manifest_path.is_relative_to(root):
        raise RuntimeReleaseError("Manifest must be outside the inventoried release")
    if root.parent.stat().st_mode & 0o077:
        raise RuntimeReleaseError("Release parent must be private")
    if sys.version_info[:2] != (3, 12):
        raise RuntimeReleaseError("Build requires Python 3.12")
    if file_digest(optmem) != OPTMEM_SNAPSHOT_SHA256:
        raise RuntimeReleaseError("OptMem is not the reviewed snapshot")
    status = subprocess.check_output(
        ["git", "-C", str(repository), "status", "--porcelain"], text=True
    )
    if status.strip():
        raise RuntimeReleaseError("Build requires a clean committed source checkout")
    commit = subprocess.check_output(
        ["git", "-C", str(repository), "rev-parse", "HEAD"], text=True
    ).strip()
    root.mkdir(mode=0o700)
    try:
        with tempfile.TemporaryDirectory(prefix="contx-release-source-") as temporary:
            source = Path(temporary) / "source"
            source.mkdir()
            archive = Path(temporary) / "source.tar"
            run(
                (
                    "git",
                    "-C",
                    str(repository),
                    "archive",
                    "--format=tar",
                    "-o",
                    str(archive),
                    commit,
                )
            )
            with tarfile.open(archive) as bundle:
                bundle.extractall(source, filter="data")
            shutil.copytree(
                python_home.resolve(strict=True), root / "python", symlinks=True
            )
            for link in (root / "python").rglob("*"):
                if link.is_symlink() and not link.resolve(strict=True).is_relative_to(
                    root / "python"
                ):
                    raise RuntimeReleaseError(
                        "Python distribution has an external link"
                    )
            # uv creates absolute shebangs: never relocate the resulting environment.
            run(
                (
                    str(uv),
                    "venv",
                    "--no-python-downloads",
                    "--python",
                    str(root / "python/bin/python3.12"),
                    str(root / "env"),
                )
            )
            requirements = Path(temporary) / "requirements.txt"
            run(
                (
                    str(uv),
                    "export",
                    "--project",
                    str(source),
                    "--frozen",
                    "--no-dev",
                    "--no-emit-project",
                    "--output-file",
                    str(requirements),
                )
            )
            run(
                (
                    str(uv),
                    "pip",
                    "sync",
                    "--python",
                    str(root / "env/bin/python3"),
                    "--require-hashes",
                    "--link-mode",
                    "copy",
                    str(requirements),
                )
            )
            wheels = Path(temporary) / "wheels"
            run(
                (
                    str(uv),
                    "build",
                    "--project",
                    str(source),
                    "--wheel",
                    "--out-dir",
                    str(wheels),
                )
            )
            (wheel,) = wheels.glob("*.whl")
            run(
                (
                    str(uv),
                    "pip",
                    "install",
                    "--python",
                    str(root / "env/bin/python3"),
                    "--no-deps",
                    "--link-mode",
                    "copy",
                    str(wheel),
                )
            )
            (root / "optmem").mkdir(mode=0o700)
            shutil.copy2(optmem, root / "optmem/memo")
            (root / "optmem/memo").chmod(0o500)
            lock_hash = file_digest(source / "uv.lock")
        version = subprocess.check_output(
            [str(root / "env/bin/python3"), "-I", "-B", "--version"], text=True
        ).strip()
        seal_tree(root)
        manifest = RuntimeManifest(
            root=str(root),
            sourceCommit=commit,
            lockSHA256=lock_hash,
            pythonVersion=version,
            files=inventory(root),
        )
        verify_manifest(manifest)
        # A hard-link publication is atomic and refuses an existing destination.
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".runtime-manifest-", dir=manifest_path.parent
        )
        temporary_manifest = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(manifest.model_dump_json(indent=2))
                stream.flush()
                os.fsync(stream.fileno())
            temporary_manifest.chmod(0o400)
            os.link(temporary_manifest, manifest_path)
        finally:
            temporary_manifest.unlink()
        return manifest
    except BaseException:
        remove_generated_tree(root)
        raise
