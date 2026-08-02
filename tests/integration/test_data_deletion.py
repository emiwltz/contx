"""Full deletion is explicit, narrow, and refuses live collection."""

from pathlib import Path

import pytest

from contx.application import DataDeletionService
from contx.daemon import DaemonLease
from contx.db import create_database_engine, upgrade_database
from contx.errors import ConfigurationError, PipelineError
from contx.settings import RuntimePaths, initialize_runtime_paths


def test_full_deletion_removes_only_configured_contx_stores(tmp_path: Path) -> None:
    paths = _paths(tmp_path / "runtime")
    initialize_runtime_paths(paths)
    upgrade_database(paths.database_file)
    engine = create_database_engine(paths.database_file)
    outside = tmp_path / "must-remain.txt"
    outside.write_text("safe", encoding="utf-8")

    result = DataDeletionService(paths=paths, engine=engine).delete_all()

    assert result.removed_stores == ("caches", "logs", "application_support")
    assert not paths.caches.exists()
    assert not paths.logs.exists()
    assert not paths.application_support.exists()
    assert outside.read_text(encoding="utf-8") == "safe"


def test_full_deletion_refuses_a_running_collector(tmp_path: Path) -> None:
    paths = _paths(tmp_path / "runtime")
    initialize_runtime_paths(paths)

    with (
        DaemonLease(paths.daemon_lock),
        pytest.raises(PipelineError, match="Stop the CONTX collection daemon"),
    ):
        DataDeletionService(paths=paths).delete_all()

    assert paths.config_file.is_file()


def test_full_deletion_refuses_nested_or_broad_roots(tmp_path: Path) -> None:
    unsafe = RuntimePaths(
        application_support=tmp_path,
        caches=tmp_path / "caches",
        logs=tmp_path / "logs",
    )

    with pytest.raises(ConfigurationError, match="must not be nested"):
        DataDeletionService(paths=unsafe).delete_all()


def _paths(root: Path) -> RuntimePaths:
    return RuntimePaths(
        application_support=root / "application-support",
        caches=root / "caches",
        logs=root / "logs",
    )
