"""Safety gate for periodic processing composition."""

from pathlib import Path

import pytest

import contx.processing.factory as processing_factory
from contx.daemon import probe_daemon_lease
from contx.errors import ConfigurationError
from contx.processing import build_context_processor
from contx.settings import (
    RUNTIME_ROOT_ENV,
    initialize_runtime_paths,
    resolve_runtime_paths,
)


def test_periodic_processor_refuses_default_disabled_background_state(
    tmp_path: Path,
) -> None:
    with pytest.raises(ConfigurationError, match="Background processing is disabled"):
        build_context_processor(environ={RUNTIME_ROOT_ENV: str(tmp_path)})

    assert not (tmp_path / "application-support" / "contx.db").exists()


def test_periodic_processor_locks_before_database_setup_and_releases_on_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = resolve_runtime_paths({RUNTIME_ROOT_ENV: str(tmp_path)})
    initialize_runtime_paths(paths)
    config = paths.config_file.read_text(encoding="utf-8").replace(
        "background_collection_enabled = false",
        "background_collection_enabled = true",
    )
    paths.config_file.write_text(config, encoding="utf-8")
    observed_lock_states: list[bool] = []

    def fail_database_setup(_database_file: Path) -> None:
        observed_lock_states.append(probe_daemon_lease(paths.processor_lock).running)
        raise RuntimeError("synthetic database setup failure")

    monkeypatch.setattr(processing_factory, "upgrade_database", fail_database_setup)

    with pytest.raises(RuntimeError, match="synthetic database setup failure"):
        build_context_processor(paths=paths)

    assert observed_lock_states == [True]
    assert not probe_daemon_lease(paths.processor_lock).running
