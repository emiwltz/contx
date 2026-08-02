"""CLI initialization integration tests."""

import stat
from pathlib import Path

import pytest
from typer.testing import CliRunner

import contx.cli.app as cli_module
from contx.cli.app import app
from contx.memory_store import RecordingMemoryStore
from contx.settings import RUNTIME_ROOT_ENV, resolve_runtime_paths

runner = CliRunner()


def test_init_is_idempotent_and_status_is_truthful(tmp_path: Path) -> None:
    environment = {RUNTIME_ROOT_ENV: str(tmp_path)}

    first = runner.invoke(app, ["init"], env=environment)
    second = runner.invoke(app, ["init"], env=environment)
    status = runner.invoke(app, ["status"], env=environment)

    assert first.exit_code == 0
    assert second.exit_code == 0
    assert status.exit_code == 0
    assert "configuration: present" in status.stdout
    assert "database: present" in status.stdout
    assert "memory: not initialized" in status.stdout
    assert "schema: current" in status.stdout
    assert "background collection: disabled" in status.stdout
    assert "collector daemon: stopped" in status.stdout

    paths = resolve_runtime_paths(environment)
    assert stat.S_IMODE(paths.config_file.stat().st_mode) == 0o600
    assert stat.S_IMODE(paths.database_file.stat().st_mode) == 0o600


def test_run_once_synthetic_uses_the_initialized_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    environment = {RUNTIME_ROOT_ENV: str(tmp_path)}
    memory = RecordingMemoryStore()
    monkeypatch.setattr(cli_module, "_build_memory_store", lambda _path: memory)

    result = runner.invoke(app, ["run-once", "--source", "synthetic"], env=environment)
    wake = runner.invoke(app, ["wake"], env=environment)

    assert result.exit_code == 0
    assert "run: succeeded" in result.stdout
    assert "observations: 5" in result.stdout
    assert "events: 2" in result.stdout
    assert "accepted candidates: 1" in result.stdout
    assert "rejected candidates: 1" in result.stdout
    assert "stored memories: 1" in result.stdout
    assert wake.exit_code == 0
    assert "Resume CONTX" in wake.stdout
    assert wake.stdout.endswith("You are awake.\n")


def test_pause_blocks_live_source_before_macos_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    environment = {RUNTIME_ROOT_ENV: str(tmp_path)}
    memory = RecordingMemoryStore()
    monkeypatch.setattr(cli_module, "_build_memory_store", lambda _path: memory)

    paused = runner.invoke(app, ["pause", "--for", "15m"], env=environment)
    status = runner.invoke(app, ["status"], env=environment)
    run = runner.invoke(app, ["run-once", "--source", "active-app"], env=environment)
    resumed = runner.invoke(app, ["resume"], env=environment)

    assert paused.exit_code == 0
    assert "collection: paused until" in paused.stdout
    assert "collection: paused" in status.stdout
    assert run.exit_code == 0
    assert "observations: 0" in run.stdout
    assert resumed.stdout == "collection: active\n"


def test_user_exclusion_can_be_added_and_listed(tmp_path: Path) -> None:
    environment = {RUNTIME_ROOT_ENV: str(tmp_path)}

    added = runner.invoke(
        app,
        [
            "exclusions",
            "add",
            "com.example.private",
            "--type",
            "app_bundle_id",
        ],
        env=environment,
    )
    listed = runner.invoke(app, ["exclusions", "list"], env=environment)

    assert added.exit_code == 0
    assert listed.exit_code == 0
    assert "app_bundle_id enabled user com.example.private" in listed.stdout


def test_empty_raw_purge_is_successful_and_audited(tmp_path: Path) -> None:
    environment = {RUNTIME_ROOT_ENV: str(tmp_path)}

    result = runner.invoke(app, ["purge"], env=environment)
    status = runner.invoke(app, ["status"], env=environment)

    assert result.exit_code == 0
    assert "purged observations: 0" in result.stdout
    assert "reclaimed bytes: 0" in result.stdout
    assert "raw usage: 0 bytes" in status.stdout


def test_capabilities_do_not_enable_or_request_sensitive_access(tmp_path: Path) -> None:
    environment = {RUNTIME_ROOT_ENV: str(tmp_path)}

    result = runner.invoke(app, ["capabilities"], env=environment)

    assert result.exit_code == 0
    assert "active_application:" in result.stdout
    assert "window_titles: disabled (disabled_by_configuration)" in result.stdout
    assert "screenshots: disabled (disabled_by_configuration)" in result.stdout
