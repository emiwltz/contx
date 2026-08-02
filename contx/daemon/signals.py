"""Temporary process-signal bridge for orderly AppKit daemon shutdown."""

from __future__ import annotations

import signal
from collections.abc import Callable
from types import FrameType
from typing import Any, Protocol, cast

from contx.errors import ConfigurationError


class SignalApi(Protocol):
    SIGINT: int
    SIGTERM: int

    def getsignal(self, signal_number: int) -> Any: ...

    def signal(self, signal_number: int, handler: Any) -> Any: ...


_DEFAULT_SIGNAL_API = cast(SignalApi, signal)


class GracefulStopSignalBridge:
    """Turn SIGINT and SIGTERM into a thread-safe daemon stop request."""

    def __init__(
        self,
        request_stop: Callable[[], None],
        *,
        signal_api: SignalApi = _DEFAULT_SIGNAL_API,
    ) -> None:
        self._request_stop = request_stop
        self._signal_api = signal_api
        self._previous: dict[int, Any] = {}

    def install(self) -> None:
        if self._previous:
            raise RuntimeError("graceful stop signal bridge is already installed")
        try:
            for signal_number in (
                self._signal_api.SIGINT,
                self._signal_api.SIGTERM,
            ):
                self._previous[signal_number] = self._signal_api.getsignal(
                    signal_number
                )
                self._signal_api.signal(signal_number, self._handle)
        except Exception as error:
            self._restore_safely()
            raise ConfigurationError(
                "Cannot install CONTX daemon stop signal handlers"
            ) from error

    def restore(self) -> None:
        if not self._previous:
            return
        failed = self._restore_safely()
        if failed:
            raise ConfigurationError("Cannot restore previous process signal handlers")

    def _restore_safely(self) -> bool:
        previous = self._previous
        self._previous = {}
        failed = False
        for signal_number, handler in previous.items():
            try:
                self._signal_api.signal(signal_number, handler)
            except Exception:
                failed = True
        return failed

    def _handle(self, _signal_number: int, _frame: FrameType | None) -> None:
        self._request_stop()

    def __enter__(self) -> GracefulStopSignalBridge:
        self.install()
        return self

    def __exit__(self, *_error: object) -> None:
        self.restore()
