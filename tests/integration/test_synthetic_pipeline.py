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
    MemoryLinkModel,
    ObservationModel,
    ProcessingRunModel,
)
from contx.db.repositories import PipelineRepository
from contx.errors import DatabaseError, MemoryStoreError, PipelineError
from contx.events.rules import VerticalSliceEventBuilder
from contx.memory_store import MemoryAppendResult, RecordingMemoryStore
from contx.memory_worker import ThresholdMemoryWorker
from contx.models import (
    CandidateStatus,
    Event,
    MemoryCandidate,
    Observation,
    ProcessingRunStatus,
    Sensitivity,
)
from contx.models.sources import UuidIdentifierSource
from tests.helpers import FixedClock

NOW = datetime(2026, 8, 2, 10, 15, tzinfo=UTC)


def test_synthetic_activity_produces_one_useful_and_one_rejected_candidate(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path)
    memory = RecordingMemoryStore()
    try:
        result = _run_pipeline(engine, memory)

        assert result.run.status is ProcessingRunStatus.SUCCEEDED
        assert len(result.observations) == 5
        assert {event.type for event in result.events} == {
            "project_resumption",
            "brief_activity",
        }
        assert len(result.accepted_candidates) == 1
        assert result.accepted_candidates[0].source_type == "project_resumption"
        assert len(result.rejected_candidates) == 1
        assert len(result.memory_links) == 1
        assert len(memory.entries) == 1
        assert result.accepted_candidates[0].status.value == "stored"
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
    memory = RecordingMemoryStore()
    try:
        first = _run_pipeline(engine, memory)
        second = _run_pipeline(engine, memory)

        assert [item.id for item in first.observations] == [
            item.id for item in second.observations
        ]
        with session_scope(engine) as session:
            repository = PipelineRepository(session)
            assert repository.count(ObservationModel) == 5
            assert repository.count(EventModel) == 2
            assert repository.count(MemoryCandidateModel) == 2
            assert repository.count(MemoryLinkModel) == 1
            assert repository.count(ProcessingRunModel) == 2
        assert first.memory_links == second.memory_links
        assert len(memory.entries) == 1
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
    memory = RecordingMemoryStore()
    try:
        with pytest.raises(PipelineError) as caught:
            _service(engine, clock, identifiers, memory).run_once(FailingCollector())

        assert private_message not in str(caught.value)
        with session_scope(engine) as session:
            run = session.scalars(select(ProcessingRunModel)).one()
            assert run.status == ProcessingRunStatus.FAILED.value
            assert run.error_code == "unexpected_pipeline_failure"
            assert run.error_summary is None
    finally:
        engine.dispose()


def test_pipeline_recovers_if_database_link_lags_memory_append(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path)

    class FailOnceAfterAppend(RecordingMemoryStore):
        def __init__(self) -> None:
            super().__init__()
            self.failed = False

        def append(self, text: str, *, idempotency_key: str) -> MemoryAppendResult:
            result = super().append(text, idempotency_key=idempotency_key)
            if not self.failed:
                self.failed = True
                raise MemoryStoreError("injected boundary failure")
            return result

    memory = FailOnceAfterAppend()
    try:
        with pytest.raises(MemoryStoreError, match="boundary failure"):
            _run_pipeline(engine, memory)

        recovered = _run_pipeline(engine, memory)

        assert len(memory.entries) == 1
        assert len(recovered.memory_links) == 1
        assert recovered.accepted_candidates[0].status.value == "stored"
        with session_scope(engine) as session:
            runs = tuple(session.scalars(select(ProcessingRunModel)))
            assert [run.status for run in runs] == [
                ProcessingRunStatus.FAILED.value,
                ProcessingRunStatus.SUCCEEDED.value,
            ]
    finally:
        engine.dispose()


def test_memory_boundary_blocks_sensitive_candidate_from_unsafe_worker(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path)
    memory = RecordingMemoryStore()
    clock = FixedClock(NOW)
    identifiers = UuidIdentifierSource()

    class SensitiveCandidateProducer(VerticalSliceCandidateProducer):
        def produce(self, events: tuple[Event, ...]) -> tuple[MemoryCandidate, ...]:
            candidates = super().produce(events)
            return tuple(
                MemoryCandidate.model_validate(
                    candidate.model_dump() | {"sensitivity": Sensitivity.SENSITIVE}
                )
                for candidate in candidates
            )

    class UnsafeAcceptingWorker:
        def decide(
            self,
            candidates: tuple[MemoryCandidate, ...],
            *,
            processed_at: datetime,
        ) -> tuple[MemoryCandidate, ...]:
            return tuple(
                candidate.decide(CandidateStatus.ACCEPTED, processed_at=processed_at)
                for candidate in candidates
            )

    service = PipelineService(
        engine=engine,
        event_builder=VerticalSliceEventBuilder(clock=clock, identifiers=identifiers),
        candidate_producer=SensitiveCandidateProducer(
            clock=clock, identifiers=identifiers
        ),
        memory_worker=UnsafeAcceptingWorker(),
        memory_store=memory,
        clock=clock,
        identifiers=identifiers,
    )
    try:
        with pytest.raises(DatabaseError, match="cannot enter durable memory"):
            service.run_once(
                SyntheticCollector.default(clock=clock, identifiers=identifiers)
            )

        assert memory.entries == ()
        with session_scope(engine) as session:
            run = tuple(session.scalars(select(ProcessingRunModel)))[-1]
            assert run.status == ProcessingRunStatus.FAILED.value
            assert run.error_code == "database_error"
    finally:
        engine.dispose()


def _engine(tmp_path: Path) -> Engine:
    path = tmp_path / "contx.db"
    upgrade_database(path)
    return create_database_engine(path)


def _run_pipeline(engine: Engine, memory: RecordingMemoryStore) -> PipelineResult:
    clock = FixedClock(NOW)
    identifiers = UuidIdentifierSource()
    collector = SyntheticCollector.default(clock=clock, identifiers=identifiers)
    return _service(engine, clock, identifiers, memory).run_once(collector)


def _service(
    engine: Engine,
    clock: FixedClock,
    identifiers: UuidIdentifierSource,
    memory: RecordingMemoryStore,
) -> PipelineService:
    return PipelineService(
        engine=engine,
        event_builder=VerticalSliceEventBuilder(clock=clock, identifiers=identifiers),
        candidate_producer=VerticalSliceCandidateProducer(
            clock=clock, identifiers=identifiers
        ),
        memory_worker=ThresholdMemoryWorker(),
        memory_store=memory,
        clock=clock,
        identifiers=identifiers,
    )
