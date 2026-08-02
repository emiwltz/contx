"""Audited foreground runner for the future supervised collection daemon."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from threading import Event
from typing import Never, Protocol

from sqlalchemy import Engine

from contx.db import session_scope
from contx.db.repositories import PipelineRepository
from contx.errors import (
    CollectorUnavailableError,
    ContxError,
    DatabaseError,
    PipelineError,
    RawStoreError,
)
from contx.models import (
    Clock,
    IdentifierSource,
    Observation,
    ProcessingRun,
)

CONTINUOUS_COLLECTION_VERSION = "continuous-collection-v1"


class StatefulCollector(Protocol):
    """Poll duration records and flush them on an orderly shutdown."""

    def collect(self) -> tuple[Observation, ...]: ...

    def close(self) -> tuple[Observation, ...]: ...


class PurgeOutcome(Protocol):
    @property
    def succeeded(self) -> bool: ...


class PurgeOperation(Protocol):
    def run(self) -> PurgeOutcome: ...


class StopSignal(Protocol):
    """Interrupt a bounded wait when shutdown has been requested."""

    def wait(self, timeout_seconds: float) -> bool: ...


class ThreadStopSignal:
    """Process-local stop signal suitable for signal-handler integration."""

    def __init__(self) -> None:
        self._event = Event()

    def request_stop(self) -> None:
        self._event.set()

    def wait(self, timeout_seconds: float) -> bool:
        return self._event.wait(timeout_seconds)


@dataclass(frozen=True, slots=True)
class ContinuousCollectionResult:
    run: ProcessingRun
    cycles: int
    observations: int


class ContinuousCollectionSession:
    """Event-loop-friendly collection lifecycle with durable failure audit."""

    def __init__(
        self,
        *,
        engine: Engine,
        collector: StatefulCollector,
        purge: PurgeOperation,
        clock: Clock,
        identifiers: IdentifierSource,
        purge_interval: timedelta,
    ) -> None:
        if purge_interval < timedelta(minutes=1):
            raise ValueError("raw purge interval must be at least one minute")
        self._engine = engine
        self._collector = collector
        self._purge = purge
        self._clock = clock
        self._identifiers = identifiers
        self._purge_interval = purge_interval
        self._running_run: ProcessingRun | None = None
        self._next_purge_at: datetime | None = None
        self._cycles = 0
        self._observation_count = 0

    def start(self) -> None:
        if self._running_run is not None:
            raise RuntimeError("continuous collection session is already started")
        started_at = self._clock.now()
        self._running_run = ProcessingRun(
            id=self._identifiers.new(),
            pipeline="continuous_collection",
            version=CONTINUOUS_COLLECTION_VERSION,
            started_at=started_at,
        )
        self._save_run(self._running_run)
        try:
            self._purge_or_fail()
            self._next_purge_at = started_at + self._purge_interval
        except Exception as error:
            self._raise_failure(error)

    def tick(self) -> int:
        """Run one non-blocking collection cycle from an external event loop."""
        self._require_running()
        try:
            now = self._clock.now()
            if self._next_purge_at is not None and now >= self._next_purge_at:
                self._purge_or_fail()
                self._next_purge_at = now + self._purge_interval
            records = self._persist(self._collector.collect())
            self._cycles += 1
            self._observation_count += len(records)
            return len(records)
        except Exception as error:
            self._raise_failure(error)

    def stop(self) -> ContinuousCollectionResult:
        running_run = self._require_running()
        try:
            self._observation_count += len(self._persist(self._collector.close()))
            run = running_run.succeed(
                ended_at=self._clock.now(),
                input_count=self._cycles,
                output_count=self._observation_count,
            )
            self._save_run(run)
        except Exception as error:
            self._raise_failure(error, cleanup=False)
        self._running_run = None
        return ContinuousCollectionResult(
            run=run,
            cycles=self._cycles,
            observations=self._observation_count,
        )

    def _require_running(self) -> ProcessingRun:
        if self._running_run is None:
            raise RuntimeError("continuous collection session is not running")
        return self._running_run

    def _raise_failure(self, error: Exception, *, cleanup: bool = True) -> Never:
        running_run = self._require_running()
        cleanup_failed = False
        if cleanup:
            try:
                self._observation_count += len(self._persist(self._collector.close()))
            except Exception:
                cleanup_failed = True
        error_code = _safe_collection_error_code(error)
        if cleanup_failed:
            error_code = f"{error_code}_cleanup_failed"[:64]
        failed = running_run.fail(
            ended_at=self._clock.now(),
            error_code=error_code,
            input_count=self._cycles,
            output_count=self._observation_count,
        )
        try:
            self._save_run(failed)
        except Exception:
            raise PipelineError(
                "Continuous collection failed and its status could not be recorded"
            ) from error
        self._running_run = None
        if isinstance(error, ContxError):
            raise error
        raise PipelineError(f"Continuous collection failed ({error_code})") from error

    def _purge_or_fail(self) -> None:
        if not self._purge.run().succeeded:
            raise RawStoreError(
                "Expired raw data could not be purged; collection stopped"
            )

    def _persist(self, records: tuple[Observation, ...]) -> tuple[Observation, ...]:
        with session_scope(self._engine) as session:
            repository = PipelineRepository(session)
            return tuple(repository.save_observation(record) for record in records)

    def _save_run(self, run: ProcessingRun) -> None:
        with session_scope(self._engine) as session:
            PipelineRepository(session).save_processing_run(run)


class ContinuousCollectionRunner:
    """Drive a continuous session from a blocking foreground loop."""

    def __init__(
        self,
        *,
        engine: Engine,
        collector: StatefulCollector,
        purge: PurgeOperation,
        clock: Clock,
        identifiers: IdentifierSource,
        stop_signal: StopSignal,
        poll_interval: timedelta,
        purge_interval: timedelta,
    ) -> None:
        if poll_interval <= timedelta(0):
            raise ValueError("collection poll interval must be positive")
        self._session = ContinuousCollectionSession(
            engine=engine,
            collector=collector,
            purge=purge,
            clock=clock,
            identifiers=identifiers,
            purge_interval=purge_interval,
        )
        self._stop_signal = stop_signal
        self._poll_interval = poll_interval

    def run(self, *, max_cycles: int | None = None) -> ContinuousCollectionResult:
        """Run until stopped; max_cycles exists for bounded tests and diagnostics."""
        if max_cycles is not None and max_cycles < 1:
            raise ValueError("maximum collection cycles must be positive")
        self._session.start()
        cycles = 0
        while True:
            self._session.tick()
            cycles += 1
            if max_cycles is not None and cycles >= max_cycles:
                break
            if self._stop_signal.wait(self._poll_interval.total_seconds()):
                break
        return self._session.stop()


def _safe_collection_error_code(error: Exception) -> str:
    if isinstance(error, CollectorUnavailableError):
        return "collector_unavailable"
    if isinstance(error, RawStoreError):
        return "raw_purge_failure"
    if isinstance(error, DatabaseError):
        return "database_error"
    if isinstance(error, PipelineError):
        return "pipeline_error"
    return "unexpected_collection_failure"
