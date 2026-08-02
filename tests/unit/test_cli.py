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


def test_agent_instructions_protect_the_memory_write_boundary() -> None:
    result = runner.invoke(app, ["instructions"])
    normalized_output = " ".join(result.stdout.split())

    assert result.exit_code == 0
    assert "run `contx wake`" in normalized_output
    assert "Never call OptMem directly" in normalized_output
    assert "Subagents must not run CONTX" in normalized_output
    assert "memory commands or submit proposals" in normalized_output


def test_recall_and_zoom_use_the_same_memory_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    memory = RecordingMemoryStore()
    memory.initialize()
    memory.append("Atlas uses local execution.", idempotency_key="atlas")
    monkeypatch.setattr(
        cli_module,
        "_configured_memory_store",
        lambda: memory,
    )

    recalled = runner.invoke(app, ["recall", "Atlas"])
    zoomed = runner.invoke(app, ["zoom", "0-1"])

    assert recalled.exit_code == 0
    assert "Atlas uses local execution." in recalled.stdout
    assert zoomed.exit_code == 0
    assert "Atlas uses local execution." in zoomed.stdout
