"""Sanitized internal daemon entrypoint behavior."""

from typing import cast

from contx.daemon.entrypoint import run_collection_daemon
from contx.daemon.factory import ConfiguredMacOSCollectionDaemon
from contx.errors import ConfigurationError


class FakeDaemon:
    def __init__(self, *, failure: Exception | None = None) -> None:
        self.failure = failure
        self.runs = 0
        self.closes = 0

    def run(self) -> None:
        self.runs += 1
        if self.failure is not None:
            raise self.failure

    def close(self) -> None:
        self.closes += 1


def test_entrypoint_runs_and_closes_the_configured_daemon() -> None:
    daemon = FakeDaemon()

    result = run_collection_daemon(
        build=lambda: cast(ConfiguredMacOSCollectionDaemon, daemon)
    )

    assert result == 0
    assert daemon.runs == daemon.closes == 1


def test_expected_failure_is_actionable_and_still_closes() -> None:
    daemon = FakeDaemon(failure=ConfigurationError("synthetic disabled state"))
    errors: list[str] = []

    result = run_collection_daemon(
        build=lambda: cast(ConfiguredMacOSCollectionDaemon, daemon),
        write_error=errors.append,
    )

    assert result == 2
    assert errors == ["CONTX collector stopped: synthetic disabled state\n"]
    assert daemon.closes == 1


def test_unexpected_failure_does_not_expose_exception_content() -> None:
    daemon = FakeDaemon(failure=RuntimeError("synthetic private title"))
    errors: list[str] = []

    result = run_collection_daemon(
        build=lambda: cast(ConfiguredMacOSCollectionDaemon, daemon),
        write_error=errors.append,
    )

    assert result == 3
    assert errors == ["CONTX collector stopped after an unexpected local failure.\n"]
    assert "private title" not in errors[0]
    assert daemon.closes == 1
