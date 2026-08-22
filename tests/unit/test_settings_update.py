"""Atomic narrow configuration changes for background activation."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from contx.errors import ConfigurationError
from contx.settings import (
    initialize_runtime_paths,
    load_settings,
    resolve_runtime_paths,
    rollback_config_mutation,
    set_background_collection_features,
)


def test_activation_flags_change_together_and_preserve_other_content(
    tmp_path: Path,
) -> None:
    paths = resolve_runtime_paths(environ={"CONTX_RUNTIME_ROOT": str(tmp_path)})
    initialize_runtime_paths(paths)
    original = paths.config_file.read_text()
    paths.config_file.write_text(
        original.replace(
            "window_titles_enabled = false",
            "window_titles_enabled = false # explicit title gate",
        )
    )
    paths.config_file.chmod(0o600)

    mutation = set_background_collection_features(paths, enabled=True)
    settings = load_settings(paths, environ={})

    assert settings.collection.background_collection_enabled
    assert settings.collection.window_titles_enabled
    assert settings.collection.screenshots_enabled
    assert "window_titles_enabled = true # explicit title gate" in (
        paths.config_file.read_text()
    )
    assert stat.S_IMODE(paths.config_file.stat().st_mode) == 0o600

    rollback_config_mutation(mutation)
    restored = load_settings(paths, environ={})
    assert not restored.collection.background_collection_enabled
    assert not restored.collection.window_titles_enabled
    assert not restored.collection.screenshots_enabled


def test_deactivation_is_idempotent(tmp_path: Path) -> None:
    paths = resolve_runtime_paths(environ={"CONTX_RUNTIME_ROOT": str(tmp_path)})
    initialize_runtime_paths(paths)
    before = paths.config_file.read_bytes()

    mutation = set_background_collection_features(paths, enabled=False)

    assert mutation.previous == mutation.updated == before
    rollback_config_mutation(mutation)
    assert paths.config_file.read_bytes() == before


def test_missing_flag_refuses_to_rewrite_any_configuration(tmp_path: Path) -> None:
    paths = resolve_runtime_paths(environ={"CONTX_RUNTIME_ROOT": str(tmp_path)})
    initialize_runtime_paths(paths)
    original = paths.config_file.read_bytes()
    changed = original.replace(b"screenshots_enabled = false\n", b"")
    paths.config_file.write_bytes(changed)
    paths.config_file.chmod(0o600)

    with pytest.raises(ConfigurationError, match="missing or duplicated"):
        set_background_collection_features(paths, enabled=True)

    assert paths.config_file.read_bytes() == changed


def test_rollback_refuses_to_overwrite_a_later_edit(tmp_path: Path) -> None:
    paths = resolve_runtime_paths(environ={"CONTX_RUNTIME_ROOT": str(tmp_path)})
    initialize_runtime_paths(paths)
    mutation = set_background_collection_features(paths, enabled=True)
    later = mutation.updated + b"\n# later operator edit\n"
    paths.config_file.write_bytes(later)
    paths.config_file.chmod(0o600)

    with pytest.raises(ConfigurationError, match="changed concurrently"):
        rollback_config_mutation(mutation)

    assert paths.config_file.read_bytes() == later


def test_flag_with_same_name_outside_collection_is_unchanged(tmp_path: Path) -> None:
    paths = resolve_runtime_paths(environ={"CONTX_RUNTIME_ROOT": str(tmp_path)})
    initialize_runtime_paths(paths)
    original = paths.config_file.read_text()
    extended = original + "\n[synthetic]\nscreenshots_enabled = false\n"
    paths.config_file.write_text(extended)
    paths.config_file.chmod(0o600)

    with pytest.raises(ConfigurationError, match="Invalid CONTX configuration"):
        set_background_collection_features(paths, enabled=True)

    assert paths.config_file.read_text() == extended
