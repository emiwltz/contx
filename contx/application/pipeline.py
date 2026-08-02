"""Restartable on-demand application pipeline."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Engine

from contx.candidates import CandidateProducer
from contx.collectors import Collector
from contx.db import session_scope
from contx.db.repositories import PipelineRepository
from contx.errors import PipelineError
from contx.events import EventBuilder
from contx.memory_worker import MemoryWorker
from contx.models import (
    CandidateStatus,
    Clock,
    Event,
    IdentifierSource,
    MemoryCandidate,
    Observation,
    ObservationStatus,
    ProcessingRun,
)

PIPELINE_VERSION = "vertical-slice-v1"


@dataclass(frozen=True, slots=True)
class PipelineResult:
    run: ProcessingRun
    observations: tuple[Observation, ...]
    events: tuple[Event, ...]
    candidates: tuple[MemoryCandidate, ...]

    @property
    def accepted_candidates(self) -> tuple[MemoryCandidate, ...]:
        return tuple(
            candidate
            for candidate in self.candidates
            if candidate.status in {CandidateStatus.ACCEPTED, CandidateStatus.STORED}
        )

    @property
    def rejected_candidates(self) -> tuple[MemoryCandidate, ...]:
        return tuple(
            candidate
            for candidate in self.candidates
            if candidate.status is CandidateStatus.REJECTED
        )


class PipelineService:
    """Execute durable stages shared by the CLI and future daemon."""

    def __init__(
        self,
        *,
        engine: Engine,
        event_builder: EventBuilder,
        candidate_producer: CandidateProducer,
        memory_worker: MemoryWorker,
        clock: Clock,
        identifiers: IdentifierSource,
    ) -> None:
        self._engine = engine
        self._event_builder = event_builder
        self._candidate_producer = candidate_producer
        self._memory_worker = memory_worker
        self._clock = clock
        self._identifiers = identifiers

    def run_once(self, collector: Collector) -> PipelineResult:
        run = ProcessingRun(
            id=self._identifiers.new(),
            pipeline="vertical_slice",
            version=PIPELINE_VERSION,
            started_at=self._clock.now(),
        )
        self._save_run(run)
        observations: tuple[Observation, ...] = ()
        events: tuple[Event, ...] = ()
        decisions: tuple[MemoryCandidate, ...] = ()
        try:
            observations = self._persist_observations(collector.collect())
            events = self._persist_events(self._event_builder.build(observations))
            pending = self._persist_candidates(self._candidate_producer.produce(events))
            decisions = self._memory_worker.decide(
                pending, processed_at=self._clock.now()
            )
            decisions = self._persist_candidates(decisions)
            observations = self._mark_observations_processed(observations)
            run = run.succeed(
                ended_at=self._clock.now(),
                input_count=len(observations),
                output_count=len(decisions),
            )
            self._save_run(run)
        except Exception as error:
            code = type(error).__name__.lower()[:64]
            failed = run.fail(ended_at=self._clock.now(), error_code=code)
            try:
                self._save_run(failed)
            except Exception:
                raise PipelineError(
                    f"Pipeline failed ({code}) and its status could not be recorded"
                ) from error
            raise PipelineError(f"Pipeline failed ({code})") from error
        return PipelineResult(
            run=run,
            observations=observations,
            events=events,
            candidates=decisions,
        )

    def _persist_observations(
        self, records: tuple[Observation, ...]
    ) -> tuple[Observation, ...]:
        with session_scope(self._engine) as session:
            repository = PipelineRepository(session)
            return tuple(repository.save_observation(record) for record in records)

    def _persist_events(self, records: tuple[Event, ...]) -> tuple[Event, ...]:
        with session_scope(self._engine) as session:
            repository = PipelineRepository(session)
            return tuple(repository.save_event(record) for record in records)

    def _persist_candidates(
        self, records: tuple[MemoryCandidate, ...]
    ) -> tuple[MemoryCandidate, ...]:
        with session_scope(self._engine) as session:
            repository = PipelineRepository(session)
            return tuple(repository.save_candidate(record) for record in records)

    def _mark_observations_processed(
        self, records: tuple[Observation, ...]
    ) -> tuple[Observation, ...]:
        processed = tuple(
            Observation.model_validate(
                record.model_dump() | {"processing_status": ObservationStatus.PROCESSED}
            )
            for record in records
        )
        return self._persist_observations(processed)

    def _save_run(self, run: ProcessingRun) -> None:
        with session_scope(self._engine) as session:
            PipelineRepository(session).save_processing_run(run)
