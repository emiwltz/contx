"""Complete immutable runtime inventory, including changed native dependencies."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from contx.macos_app import runtime_release as release


def make_release(tmp_path, monkeypatch):
    tmp_path.chmod(0o700)
    root = tmp_path / "release"
    (root / "env/bin").mkdir(parents=True)
    (root / "python/bin").mkdir(parents=True)
    (root / "optmem").mkdir()
    for name in ["python/bin/python3.12", "optmem/memo"]:
        path = root / name
        path.write_bytes(b"synthetic executable")
        path.chmod(0o700)
    (root / "env/bin/python3").symlink_to("../../python/bin/python3.12")
    (root / "module.py").write_text("synthetic module")
    (root / "native.dylib").write_bytes(b"synthetic native library")
    monkeypatch.setattr(
        release,
        "OPTMEM_SNAPSHOT_SHA256",
        hashlib.sha256(b"synthetic executable").hexdigest(),
    )
    release.seal_tree(root)
    manifest = release.RuntimeManifest(
        root=str(root),
        sourceCommit="a" * 40,
        lockSHA256="b" * 64,
        pythonVersion="Python 3.12.test",
        files=release.inventory(root),
    )
    path = tmp_path / "runtime.json"
    path.write_text(manifest.model_dump_json())
    return manifest, path


def test_verifies_complete_release_and_internal_symlink(tmp_path, monkeypatch):
    manifest, path = make_release(tmp_path, monkeypatch)
    assert release.load_manifest(path) == manifest


@pytest.mark.parametrize(
    "relative", ["python/bin/python3.12", "module.py", "native.dylib", "optmem/memo"]
)
def test_rejects_altered_execution_bytes(tmp_path, monkeypatch, relative):
    manifest, _ = make_release(tmp_path, monkeypatch)
    target = Path(manifest.root) / relative
    mode = target.stat().st_mode & 0o777
    target.chmod(0o600)
    target.write_bytes(b"modified")
    target.chmod(mode)
    with pytest.raises(release.RuntimeReleaseError, match="inventory"):
        release.verify_manifest(manifest)


@pytest.mark.parametrize("change", ["extra", "missing", "writable", "external-link"])
def test_rejects_changed_tree_structure_and_permissions(tmp_path, monkeypatch, change):
    manifest, _ = make_release(tmp_path, monkeypatch)
    root = Path(manifest.root)
    root.chmod(0o700)
    if change == "extra":
        (root / "extra.py").write_text("extra")
        (root / "extra.py").chmod(0o400)
    elif change == "missing":
        (root / "module.py").unlink()
    elif change == "writable":
        (root / "module.py").chmod(0o600)
    else:
        (root / "module.py").unlink()
        (root / "module.py").symlink_to("/usr/bin/true")
    root.chmod(0o500)
    with pytest.raises(release.RuntimeReleaseError):
        release.verify_manifest(manifest)


def test_cleanup_only_removes_owned_unpublished_release(tmp_path, monkeypatch):
    manifest, _ = make_release(tmp_path, monkeypatch)
    previous = tmp_path / "previous"
    previous.mkdir()
    marker = previous / "keep"
    marker.write_text("previous release")
    release.remove_generated_tree(Path(manifest.root))
    assert marker.read_text() == "previous release"


def test_failed_build_removes_only_new_release(tmp_path, monkeypatch):
    import io
    import tarfile

    previous = tmp_path / "previous"
    previous.mkdir()
    (previous / "keep").write_text("keep")
    python = tmp_path / "python-source"
    python.mkdir()
    optmem = tmp_path / "memo"
    optmem.write_bytes(b"synthetic executable")
    monkeypatch.setattr(
        release,
        "OPTMEM_SNAPSHOT_SHA256",
        hashlib.sha256(optmem.read_bytes()).hexdigest(),
    )
    monkeypatch.setattr(
        release.subprocess,
        "check_output",
        lambda command, **kwargs: "" if "status" in command else "a" * 40,
    )

    def failing_toolchain(command):
        if command[0] == "git":
            with tarfile.open(command[command.index("-o") + 1], "w") as archive:
                payload = b"version = 1\n"
                info = tarfile.TarInfo("uv.lock")
                info.size = len(payload)
                archive.addfile(info, io.BytesIO(payload))
        else:
            raise RuntimeError("interrupted environment build")

    tmp_path.chmod(0o700)
    with pytest.raises(RuntimeError, match="interrupted"):
        release.build_private_runtime(
            tmp_path / "new",
            tmp_path / "new.json",
            repository=tmp_path,
            python_home=python,
            optmem=optmem,
            uv=Path("/fake/uv"),
            run=failing_toolchain,
        )
    assert not (tmp_path / "new").exists()
    assert not (tmp_path / "new.json").exists()
    assert (previous / "keep").read_text() == "keep"


def test_build_refuses_existing_version_without_changes(tmp_path):
    tmp_path.chmod(0o700)
    root = tmp_path / "existing"
    root.mkdir()
    (root / "keep").write_text("keep")
    with pytest.raises(release.RuntimeReleaseError, match="must be new"):
        release.build_private_runtime(
            root,
            tmp_path / "manifest",
            repository=tmp_path,
            python_home=tmp_path,
            optmem=tmp_path,
            uv=tmp_path,
        )
    assert (root / "keep").read_text() == "keep"
