"""Runtime path resolution and protection tests."""

import stat
from pathlib import Path

import pytest

from contx.errors import ConfigurationError
from contx.settings import (
    RUNTIME_ROOT_ENV,
    initialize_runtime_paths,
    resolve_runtime_paths,
)


def test_production_paths_use_native_macos_locations(tmp_path: Path) -> None:
    paths = resolve_runtime_paths({}, home=tmp_path)

    assert paths.application_support == tmp_path / "Library/Application Support/CONTX"
    assert paths.caches == tmp_path / "Library/Caches/CONTX"
    assert paths.logs == tmp_path / "Library/Logs/CONTX"


def test_override_redirects_all_runtime_roots(tmp_path: Path) -> None:
    paths = resolve_runtime_paths({RUNTIME_ROOT_ENV: str(tmp_path)})

    assert paths.application_support == tmp_path / "application-support"
    assert paths.caches == tmp_path / "caches"
    assert paths.logs == tmp_path / "logs"


def test_override_must_be_absolute() -> None:
    with pytest.raises(ConfigurationError, match="must be an absolute path"):
        resolve_runtime_paths({RUNTIME_ROOT_ENV: "relative/path"})


def test_initialization_is_idempotent_and_private(tmp_path: Path) -> None:
    paths = resolve_runtime_paths({RUNTIME_ROOT_ENV: str(tmp_path)})

    initialize_runtime_paths(paths)
    initialize_runtime_paths(paths)

    for directory in paths.owned_directories:
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert stat.S_IMODE(paths.config_file.stat().st_mode) == 0o600
    assert "background_collection_enabled = false" in paths.config_file.read_text()


def test_initialization_rejects_a_symlinked_owned_path(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    paths = resolve_runtime_paths({RUNTIME_ROOT_ENV: str(tmp_path / "runtime")})
    paths.application_support.parent.mkdir(parents=True)
    paths.application_support.symlink_to(target, target_is_directory=True)

    with pytest.raises(ConfigurationError, match="not a safe directory"):
        initialize_runtime_paths(paths)
