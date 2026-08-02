"""Temporary daemon signal-handler installation and restoration."""

from typing import Any

import pytest

from contx.daemon import GracefulStopSignalBridge
from contx.errors import ConfigurationError


class FakeSignalApi:
    SIGINT = 2
    SIGTERM = 15

    def __init__(self, *, fail_on: int | None = None) -> None:
        self.fail_on = fail_on
        self.handlers: dict[int, Any] = {
            self.SIGINT: "previous-int",
            self.SIGTERM: "previous-term",
        }
        self.changes: list[tuple[int, Any]] = []

    def getsignal(self, signal_number: int) -> Any:
        return self.handlers[signal_number]

    def signal(self, signal_number: int, handler: Any) -> Any:
        self.changes.append((signal_number, handler))
        if self.fail_on == signal_number and callable(handler):
            raise RuntimeError("synthetic signal registration failure")
        previous = self.handlers[signal_number]
        self.handlers[signal_number] = handler
        return previous


def test_signal_handlers_request_stop_and_restore_previous_state() -> None:
    api = FakeSignalApi()
    stops = 0

    def request_stop() -> None:
        nonlocal stops
        stops += 1

    with GracefulStopSignalBridge(request_stop, signal_api=api):
        assert callable(api.handlers[api.SIGINT])
        assert callable(api.handlers[api.SIGTERM])
        api.handlers[api.SIGTERM](api.SIGTERM, None)

    assert stops == 1
    assert api.handlers == {2: "previous-int", 15: "previous-term"}


def test_partial_signal_installation_failure_restores_prior_handler() -> None:
    api = FakeSignalApi(fail_on=FakeSignalApi.SIGTERM)

    with pytest.raises(ConfigurationError, match="Cannot install"):
        GracefulStopSignalBridge(lambda: None, signal_api=api).install()

    assert api.handlers == {2: "previous-int", 15: "previous-term"}
