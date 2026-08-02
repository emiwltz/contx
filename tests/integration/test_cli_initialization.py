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
