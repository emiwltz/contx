"""CLI initialization integration tests."""

import stat
from pathlib import Path

from typer.testing import CliRunner

from contx.cli.app import app
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
    assert "schema: current" in status.stdout
    assert "background collection: disabled" in status.stdout

    paths = resolve_runtime_paths(environment)
    assert stat.S_IMODE(paths.config_file.stat().st_mode) == 0o600
    assert stat.S_IMODE(paths.database_file.stat().st_mode) == 0o600
