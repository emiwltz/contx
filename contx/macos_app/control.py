"""Versioned, content-free control boundary for the native macOS host."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import timedelta
from enum import StrEnum
from pathlib import Path
from typing import Protocol, TextIO

from sqlalchemy import Engine

from contx.collection import CollectionControlService, read_collection_pause_state
from contx.daemon import probe_daemon_lease
from contx.db import create_database_engine
from contx.errors import ConfigurationError, ContxError
from contx.models import Clock, SystemClock
from contx.settings import RuntimePaths, load_settings, resolve_runtime_paths

CONTROL_SCHEMA_VERSION = 1
MAXIMUM_PAUSE_SECONDS = 30 * 24 * 60 * 60


class NativeHostState(StrEnum):
    """Content-free collection state rendered by the native menu."""

    DISABLED = "disabled"
    STOPPED = "stopped"
    PAUSED = "paused"
    ACTIVE = "active"


@dataclass(frozen=True, slots=True)
class NativeHostControlStatus:
    """Versioned status that contains no observed user content."""

    schema_version: int
    state: NativeHostState
    background_enabled: bool
    collection_paused: bool
    collector_running: bool


class EngineFactory(Protocol):
    def __call__(self, database_path: Path) -> Engine: ...


class NativeHostControlService:
    """Delegate native menu actions to existing durable Python services."""

    def __init__(
        self,
        *,
        paths: RuntimePaths,
        clock: Clock,
        engine_factory: EngineFactory = create_database_engine,
    ) -> None:
        self._paths = paths
        self._clock = clock
        self._engine_factory = engine_factory

    def status(self) -> NativeHostControlStatus:
        """Read configuration, pause, and live lease state without mutation."""
        settings = load_settings(self._paths, environ={})
        paused = read_collection_pause_state(
            self._paths.database_file,
            at=self._clock.now(),
        )
        collector_running = (
            probe_daemon_lease(self._paths.daemon_lock).running
            if self._paths.processing.is_dir()
            else False
        )
        if not settings.collection.background_collection_enabled:
            state = NativeHostState.DISABLED
        elif not collector_running:
            state = NativeHostState.STOPPED
        elif paused:
            state = NativeHostState.PAUSED
        else:
            state = NativeHostState.ACTIVE
        return NativeHostControlStatus(
            schema_version=CONTROL_SCHEMA_VERSION,
            state=state,
            background_enabled=settings.collection.background_collection_enabled,
            collection_paused=paused,
            collector_running=collector_running,
        )

    def pause(self, *, duration: timedelta | None = None) -> NativeHostControlStatus:
        """Pause through the existing durable collection-control service."""
        engine = self._engine_factory(self._paths.database_file)
        try:
            CollectionControlService(engine=engine, clock=self._clock).pause(
                duration=duration
            )
        finally:
            engine.dispose()
        return self.status()

    def resume(self) -> NativeHostControlStatus:
        """Resume only when configuration and the live collector agree."""
        before = self.status()
        if not before.background_enabled:
            raise ConfigurationError(
                "Native host cannot resume while background collection is disabled"
            )
        if not before.collector_running:
            raise ConfigurationError(
                "Native host cannot resume while the collector is stopped"
            )
        engine = self._engine_factory(self._paths.database_file)
        try:
            CollectionControlService(engine=engine, clock=self._clock).resume()
        finally:
            engine.dispose()
        return self.status()


def run_native_host_control(
    arguments: Sequence[str],
    *,
    environ: Mapping[str, str] | None = None,
    clock: Clock | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Execute one bounded native-host command with sanitized failures."""
    output = stdout or sys.stdout
    error_output = stderr or sys.stderr
    try:
        parsed = _parse_arguments(arguments)
        service = NativeHostControlService(
            paths=resolve_runtime_paths(environ),
            clock=clock or SystemClock(),
        )
        if parsed.command == "status":
            result = service.status()
        elif parsed.command == "pause":
            duration = (
                None if parsed.seconds is None else timedelta(seconds=parsed.seconds)
            )
            result = service.pause(duration=duration)
        elif parsed.command == "resume":
            result = service.resume()
        else:  # pragma: no cover - argparse constrains the command
            raise AssertionError("unreachable native host command")
    except (ContxError, ValueError) as error:
        error_output.write(f"CONTX native control failed: {error}\n")
        return 2
    json.dump(asdict(result), output, sort_keys=True, separators=(",", ":"))
    output.write("\n")
    return 0


def _parse_arguments(arguments: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="contx-native-control")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("status")
    pause_parser = subparsers.add_parser("pause")
    pause_parser.add_argument(
        "--seconds",
        type=_pause_seconds,
        default=None,
    )
    subparsers.add_parser("resume")
    return parser.parse_args(tuple(arguments))


def _pause_seconds(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("pause seconds must be an integer") from error
    if not 1 <= parsed <= MAXIMUM_PAUSE_SECONDS:
        raise argparse.ArgumentTypeError(
            f"pause seconds must be between 1 and {MAXIMUM_PAUSE_SECONDS}"
        )
    return parsed


def main() -> None:
    raise SystemExit(run_native_host_control(sys.argv[1:]))


if __name__ == "__main__":
    main()
