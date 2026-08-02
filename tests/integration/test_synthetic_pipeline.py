"""Synthetic vertical-slice behavior and replay tests."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine, select

from contx.application import PipelineResult, PipelineService
from contx.candidates.rules import VerticalSliceCandidateProducer
from contx.collectors.synthetic import SyntheticCollector
from contx.db import create_database_engine, session_scope, upgrade_database
from contx.db.models import (
    EventModel,
    MemoryCandidateModel,
    ObservationModel,
    ProcessingRunModel,
)
from contx.db.repositories import PipelineRepository
from contx.errors import PipelineError
from contx.events.rules import VerticalSliceEventBuilder
from contx.memory_worker import ThresholdMemoryWorker
from contx.models import Observation, ProcessingRunStatus
from contx.models.sources import UuidIdentifierSource
from tests.helpers import FixedClock

NOW = datetime(2026, 8, 2, 10, 15, tzinfo=UTC)


def test_synthetic_activity_produces_one_useful_and_one_rejected_candidate(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path)
    try:
        result = _run_pipeline(engine)

        assert result.run.status is ProcessingRunStatus.SUCCEEDED
        assert len(result.observations) == 5
        assert {event.type for event in result.events} == {
            "project_resumption",
            "brief_activity",
        }
        assert len(result.accepted_candidates) == 1
        assert result.accepted_candidates[0].source_type == "project_resumption"
        assert len(result.rejected_candidates) == 1
        assert (
            result.rejected_candidates[0].rejection_reason
            == "unsupported_or_below_memory_threshold"
        )
        assert all(item.window_title is None for item in result.observations)
        assert all(item.artifact_path is None for item in result.observations)
    finally:
        engine.dispose()


def test_synthetic_replay_does_not_duplicate_derived_records(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    try:
        first = _run_pipeline(engine)
        second = _run_pipeline(engine)

        assert [item.id for item in first.observations] == [
            item.id for item in second.observations
        ]
        with session_scope(engine) as session:
            repository = PipelineRepository(session)
            assert repository.count(ObservationModel) == 5
            assert repository.count(EventModel) == 2
            assert repository.count(MemoryCandidateModel) == 2
            assert repository.count(ProcessingRunModel) == 2
    finally:
        engine.dispose()


def test_pipeline_failure_records_no_private_exception_message(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    private_message = "synthetic-private-payload"

    class FailingCollector:
        def collect(self) -> tuple[Observation, ...]:
            raise RuntimeError(private_message)

    clock = FixedClock(NOW)
    identifiers = UuidIdentifierSource()
    try:
        with pytest.raises(PipelineError) as caught:
            _service(engine, clock, identifiers).run_once(FailingCollector())

        assert private_message not in str(caught.value)
        with session_scope(engine) as session:
            run = session.scalars(select(ProcessingRunModel)).one()
            assert run.status == ProcessingRunStatus.FAILED.value
            assert run.error_code == "unexpected_pipeline_failure"
            assert run.error_summary is None
    finally:
        engine.dispose()


def _engine(tmp_path: Path) -> Engine:
    path = tmp_path / "contx.db"
    upgrade_database(path)
    return create_database_engine(path)


def _run_pipeline(engine: Engine) -> PipelineResult:
    clock = FixedClock(NOW)
    identifiers = UuidIdentifierSource()
    collector = SyntheticCollector.default(clock=clock, identifiers=identifiers)
    return _service(engine, clock, identifiers).run_once(collector)


def _service(
    engine: Engine,
    clock: FixedClock,
    identifiers: UuidIdentifierSource,
) -> PipelineService:
    return PipelineService(
        engine=engine,
        event_builder=VerticalSliceEventBuilder(clock=clock, identifiers=identifiers),
        candidate_producer=VerticalSliceCandidateProducer(
            clock=clock, identifiers=identifiers
        ),
        memory_worker=ThresholdMemoryWorker(),
        clock=clock,
        identifiers=identifiers,
    )
