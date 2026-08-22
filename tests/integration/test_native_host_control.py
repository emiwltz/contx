"""Content-free native host control uses the durable collection contracts."""

from __future__ import annotations

import io
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from contx.collection import CollectionControlService
from contx.daemon import DaemonLease
from contx.db import create_database_engine, upgrade_database
from contx.errors import ConfigurationError
from contx.macos_app import NativeHostControlService, NativeHostState
from contx.macos_app import control as native_control
from contx.macos_app.control import run_native_host_control
from contx.settings import RuntimePaths, initialize_runtime_paths
from tests.helpers import FixedClock

NOW = datetime(2026, 8, 22, 15, 0, tzinfo=UTC)


def test_status_distinguishes_disabled_stopped_paused_and_active(
    tmp_path: Path,
) -> None:
    paths = _initialized_paths(tmp_path)
    service = NativeHostControlService(paths=paths, clock=FixedClock(NOW))

    assert service.status().state is NativeHostState.DISABLED
    _enable_background(paths)
    assert service.status().state is NativeHostState.STOPPED

    lease = DaemonLease(paths.daemon_lock)
    lease.acquire()
    try:
        assert service.status().state is NativeHostState.ACTIVE
        paused = service.pause()
        assert paused.state is NativeHostState.PAUSED
        assert paused.collection_paused
        resumed = service.resume()
        assert resumed.state is NativeHostState.ACTIVE
        assert not resumed.collection_paused
    finally:
        lease.release()


def test_disabled_status_does_not_open_collection_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _initialized_paths(tmp_path)

    def unexpected_pause_read(_database_path: Path, *, at: datetime) -> bool:
        raise AssertionError("disabled native status must not open SQLite")

    monkeypatch.setattr(
        native_control,
        "read_collection_pause_state",
        unexpected_pause_read,
    )

    status = NativeHostControlService(
        paths=paths,
        clock=FixedClock(NOW),
    ).status()

    assert status.state is NativeHostState.DISABLED
    assert status.collection_paused
    assert not status.collector_running


def test_repeated_disabled_status_does_not_mutate_runtime_files(
    tmp_path: Path,
) -> None:
    paths = _initialized_paths(tmp_path)
    service = NativeHostControlService(paths=paths, clock=FixedClock(NOW))
    before = _runtime_file_snapshot(paths)

    statuses = [service.status() for _ in range(3)]

    assert all(status.state is NativeHostState.DISABLED for status in statuses)
    assert _runtime_file_snapshot(paths) == before


def test_resume_fails_closed_without_enabled_live_collector(tmp_path: Path) -> None:
    paths = _initialized_paths(tmp_path)
    service = NativeHostControlService(paths=paths, clock=FixedClock(NOW))

    with pytest.raises(ConfigurationError, match="disabled"):
        service.resume()

    _enable_background(paths)
    with pytest.raises(ConfigurationError, match="stopped"):
        service.resume()


def test_pause_duration_and_json_protocol_are_bounded_and_content_free(
    tmp_path: Path,
) -> None:
    paths = _initialized_paths(tmp_path)
    output = io.StringIO()
    error_output = io.StringIO()

    exit_status = run_native_host_control(
        ("pause", "--seconds", "900"),
        environ={"CONTX_RUNTIME_ROOT": str(tmp_path)},
        clock=FixedClock(NOW),
        stdout=output,
        stderr=error_output,
    )

    assert exit_status == 0
    assert error_output.getvalue() == ""
    payload = json.loads(output.getvalue())
    assert payload == {
        "background_enabled": False,
        "collection_paused": True,
        "collector_running": False,
        "schema_version": 1,
        "state": "disabled",
    }
    engine = create_database_engine(paths.database_file)
    try:
        control = CollectionControlService(
            engine=engine,
            clock=FixedClock(NOW),
        ).control()
        assert control.pause_until == NOW + timedelta(minutes=15)
    finally:
        engine.dispose()


def test_control_failure_is_sanitized_and_emits_no_json(tmp_path: Path) -> None:
    _initialized_paths(tmp_path)
    output = io.StringIO()
    error_output = io.StringIO()

    exit_status = run_native_host_control(
        ("resume",),
        environ={"CONTX_RUNTIME_ROOT": str(tmp_path)},
        clock=FixedClock(NOW),
        stdout=output,
        stderr=error_output,
    )

    assert exit_status == 2
    assert output.getvalue() == ""
    assert error_output.getvalue() == (
        "CONTX native control failed: Native host cannot resume while "
        "background collection is disabled\n"
    )


def _initialized_paths(root: Path) -> RuntimePaths:
    paths = RuntimePaths(
        application_support=root / "application-support",
        caches=root / "caches",
        logs=root / "logs",
    )
    initialize_runtime_paths(paths)
    upgrade_database(paths.database_file)
    engine = create_database_engine(paths.database_file)
    try:
        CollectionControlService(engine=engine, clock=FixedClock(NOW)).initialize()
    finally:
        engine.dispose()
    return paths


def _enable_background(paths: RuntimePaths) -> None:
    configured = paths.config_file.read_text(encoding="utf-8").replace(
        "background_collection_enabled = false",
        "background_collection_enabled = true",
    )
    paths.config_file.write_text(configured, encoding="utf-8")
    paths.config_file.chmod(0o600)


def _runtime_file_snapshot(
    paths: RuntimePaths,
) -> dict[str, tuple[bytes, int, int]]:
    snapshot: dict[str, tuple[bytes, int, int]] = {}
    for root in (paths.application_support, paths.caches, paths.logs):
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            file_status = path.stat()
            relative = path.relative_to(root)
            snapshot[f"{root.name}/{relative}"] = (
                path.read_bytes(),
                file_status.st_mtime_ns,
                file_status.st_ctime_ns,
            )
    return snapshot
