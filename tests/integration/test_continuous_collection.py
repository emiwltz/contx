"""Audited continuous collection runner integration tests."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import Engine, select

from contx.application import ContinuousCollectionRunner
from contx.application.continuous_collection import StatefulCollector
from contx.db import (
    create_database_engine,
    session_scope,
    upgrade_database,
)
from contx.db.models import ObservationModel, ProcessingRunModel
from contx.errors import CollectorUnavailableError, RawStoreError
from contx.models import Observation, ProcessingRunStatus, SourceType
from tests.helpers import SequenceIdentifiers

START = datetime(2026, 8, 2, 10, 0, tzinfo=UTC)
RUN_ID = UUID(int=500)


class MutableClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def now(self) -> datetime:
        return self.value


class AdvancingStopSignal:
    def __init__(self, clock: MutableClock) -> None:
        self._clock = clock
        self.waits = 0

    def wait(self, timeout_seconds: float) -> bool:
        self.waits += 1
        self._clock.value += timedelta(seconds=timeout_seconds)
        return False


@dataclass(frozen=True)
class FakePurgeOutcome:
    succeeded: bool


class FakePurge:
    def __init__(self, *, succeeded: bool = True) -> None:
        self._succeeded = succeeded
        self.calls = 0

    def run(self) -> FakePurgeOutcome:
        self.calls += 1
        return FakePurgeOutcome(succeeded=self._succeeded)


class SequenceCollector:
    def __init__(
        self,
        cycles: tuple[tuple[Observation, ...], ...],
        *,
        closing: tuple[Observation, ...] = (),
    ) -> None:
        self._cycles = iter(cycles)
        self._closing = closing
        self.closed = 0

    def collect(self) -> tuple[Observation, ...]:
        return next(self._cycles)

    def close(self) -> tuple[Observation, ...]:
        self.closed += 1
        result = self._closing
        self._closing = ()
        return result


class FailingCollector:
    def collect(self) -> tuple[Observation, ...]:
        raise CollectorUnavailableError("synthetic private source detail")

    def close(self) -> tuple[Observation, ...]:
        return ()


def test_runner_purges_persists_flushes_and_audits_success(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    clock = MutableClock(START)
    purge = FakePurge()
    first = _observation(1, START + timedelta(seconds=10))
    closing = _observation(2, START + timedelta(seconds=20))
    collector = SequenceCollector(((first,), ()), closing=(closing,))
    try:
        result = _runner(engine, collector, purge, clock).run(max_cycles=2)

        assert result.run.status is ProcessingRunStatus.SUCCEEDED
        assert result.cycles == 2
        assert result.observations == 2
        assert purge.calls == 2
        assert collector.closed == 1
        with session_scope(engine) as session:
            observations = tuple(session.scalars(select(ObservationModel)))
            run = session.scalars(select(ProcessingRunModel)).one()
        assert len(observations) == 2
        assert run.pipeline == "continuous_collection"
        assert run.status == ProcessingRunStatus.SUCCEEDED.value
        assert run.input_count == 2
        assert run.output_count == 2
    finally:
        engine.dispose()


def test_runner_purges_again_on_schedule(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    clock = MutableClock(START)
    purge = FakePurge()
    collector = SequenceCollector(((), (), ()))
    try:
        _runner(engine, collector, purge, clock).run(max_cycles=3)

        assert purge.calls == 3
    finally:
        engine.dispose()


def test_failed_startup_purge_prevents_collection_and_is_audited(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path)
    clock = MutableClock(START)
    purge = FakePurge(succeeded=False)
    collector = SequenceCollector(((_observation(1, START),),))
    try:
        with pytest.raises(RawStoreError, match="collection stopped"):
            _runner(engine, collector, purge, clock).run(max_cycles=1)

        with session_scope(engine) as session:
            assert session.scalar(select(ObservationModel)) is None
            run = session.scalars(select(ProcessingRunModel)).one()
        assert run.status == ProcessingRunStatus.FAILED.value
        assert run.error_code == "raw_purge_failure"
        assert run.error_summary is None
    finally:
        engine.dispose()


def test_private_collector_error_is_not_written_to_audit_log(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    clock = MutableClock(START)
    try:
        with pytest.raises(CollectorUnavailableError, match="private source detail"):
            _runner(engine, FailingCollector(), FakePurge(), clock).run(max_cycles=1)

        with session_scope(engine) as session:
            run = session.scalars(select(ProcessingRunModel)).one()
        assert run.status == ProcessingRunStatus.FAILED.value
        assert run.error_code == "collector_unavailable"
        assert run.error_summary is None
    finally:
        engine.dispose()


def _runner(
    engine: Engine,
    collector: StatefulCollector,
    purge: FakePurge,
    clock: MutableClock,
) -> ContinuousCollectionRunner:
    return ContinuousCollectionRunner(
        engine=engine,
        collector=collector,
        purge=purge,
        clock=clock,
        identifiers=SequenceIdentifiers((RUN_ID,)),
        stop_signal=AdvancingStopSignal(clock),
        poll_interval=timedelta(seconds=60),
        purge_interval=timedelta(seconds=60),
    )


def _observation(identifier: int, captured_at: datetime) -> Observation:
    return Observation(
        id=UUID(int=identifier),
        idempotency_key=f"{identifier:064x}",
        source_type=SourceType.ACTIVE_APP,
        captured_at=captured_at,
        started_at=captured_at,
        ended_at=captured_at,
        app_name="Synthetic Editor",
        app_bundle_id="com.example.editor",
        expires_at=captured_at + timedelta(hours=48),
        created_at=captured_at,
    )


def _engine(tmp_path: Path) -> Engine:
    path = tmp_path / "contx.db"
    upgrade_database(path)
    return create_database_engine(path)
