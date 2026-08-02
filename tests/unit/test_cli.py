"""CLI foundation tests."""

import pytest
from typer.testing import CliRunner

import contx.cli.app as cli_module
from contx import __version__
from contx.cli.app import app
from contx.memory_store import RecordingMemoryStore

runner = CliRunner()


def test_help_is_available() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "Local-first personal context memory" in result.stdout


def test_version_is_available() -> None:
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.stdout.strip() == __version__


def test_memory_help_exposes_separate_historical_and_active_recovery() -> None:
    result = runner.invoke(app, ["memory", "--help"])

    assert result.exit_code == 0
    assert "rebuild-active" in result.stdout
    assert "invalidate-summary" in result.stdout


def test_root_help_exposes_loopback_web_interface() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "web" in result.stdout
    assert "127.0.0.1" in result.stdout


def test_root_help_exposes_controlled_pilot_tools() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "pilot" in result.stdout

    pilot_help = runner.invoke(app, ["pilot", "--help"])
    assert pilot_help.exit_code == 0
    assert "prepare" in pilot_help.stdout
    assert "validate" in pilot_help.stdout
    assert "report" in pilot_help.stdout
    assert "sample-resources" in pilot_help.stdout


def test_agent_instructions_protect_the_memory_write_boundary() -> None:
    result = runner.invoke(app, ["instructions"])
    normalized_output = " ".join(result.stdout.split())

    assert result.exit_code == 0
    assert "run `contx wake`" in normalized_output
    assert "Never call OptMem directly" in normalized_output
    assert "only active SQLite-backed memories" in normalized_output
    assert "historical tools" in normalized_output
    assert "`contx memory rebuild-active`" in normalized_output
    assert "`contx proposals adopt|reject`" in normalized_output
    assert "Subagents must not run CONTX" in normalized_output
    assert "memory commands or submit proposals" in normalized_output


def test_recall_and_zoom_use_the_same_historical_memory_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    memory = RecordingMemoryStore()
    memory.initialize()
    memory.append("Atlas uses local execution.", idempotency_key="atlas")
    monkeypatch.setattr(
        cli_module,
        "_configured_historical_memory_store",
        lambda: memory,
    )

    recalled = runner.invoke(app, ["recall", "Atlas"])
    zoomed = runner.invoke(app, ["zoom", "0-1"])

    assert recalled.exit_code == 0
    assert "Atlas uses local execution." in recalled.stdout
    assert zoomed.exit_code == 0
    assert "Atlas uses local execution." in zoomed.stdout
