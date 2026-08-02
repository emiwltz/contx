"""Restartable on-demand application pipeline."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Engine

from contx.candidates import CandidateProducer
from contx.collectors import Collector
from contx.db import session_scope
from contx.db.repositories import PipelineRepository
from contx.errors import (
    CollectorUnavailableError,
    ContxError,
    DatabaseError,
    MemoryStoreError,
    PipelineError,
)
from contx.events import EventBuilder
from contx.memory_store import MemoryStore
from contx.memory_worker import MemoryWorker
from contx.models import (
    CandidateStatus,
    Clock,
    Event,
    IdentifierSource,
    MemoryCandidate,
    MemoryLink,
    MemoryProvenance,
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
    memory_links: tuple[MemoryLink, ...]
    memory_maintenance_required: bool

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
        memory_store: MemoryStore,
        clock: Clock,
        identifiers: IdentifierSource,
    ) -> None:
        self._engine = engine
        self._event_builder = event_builder
        self._candidate_producer = candidate_producer
        self._memory_worker = memory_worker
        self._memory_store = memory_store
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
        memory_links: tuple[MemoryLink, ...] = ()
        maintenance_required = False
        try:
            self._memory_store.initialize()
            observations = self._persist_observations(collector.collect())
            events = self._persist_events(self._event_builder.build(observations))
            pending = self._persist_candidates(self._candidate_producer.produce(events))
            decisions = self._memory_worker.decide(
                pending, processed_at=self._clock.now()
            )
            decisions = self._persist_candidates(decisions)
            decisions, memory_links, maintenance_required = self._persist_memories(
                decisions, events
            )
            observations = self._mark_observations_processed(observations)
            run = run.succeed(
                ended_at=self._clock.now(),
                input_count=len(observations),
                output_count=len(decisions),
            )
            self._save_run(run)
        except Exception as error:
            code = _safe_error_code(error)
            failed = run.fail(ended_at=self._clock.now(), error_code=code)
            try:
                self._save_run(failed)
            except Exception:
                raise PipelineError(
                    f"Pipeline failed ({code}) and its status could not be recorded"
                ) from error
            if isinstance(error, ContxError):
                raise
            raise PipelineError(f"Pipeline failed ({code})") from error
        return PipelineResult(
            run=run,
            observations=observations,
            events=events,
            candidates=decisions,
            memory_links=memory_links,
            memory_maintenance_required=maintenance_required,
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

    def _persist_memories(
        self,
        candidates: tuple[MemoryCandidate, ...],
        events: tuple[Event, ...],
    ) -> tuple[tuple[MemoryCandidate, ...], tuple[MemoryLink, ...], bool]:
        events_by_id = {event.id: event for event in events}
        resolved_candidates: list[MemoryCandidate] = []
        links: list[MemoryLink] = []
        maintenance_required = False
        for candidate in candidates:
            if candidate.status not in {
                CandidateStatus.ACCEPTED,
                CandidateStatus.STORED,
            }:
                resolved_candidates.append(candidate)
                continue

            with session_scope(self._engine) as session:
                existing = PipelineRepository(session).memory_link_by_candidate_id(
                    candidate.id
                )
            if existing is not None:
                stored = (
                    candidate
                    if candidate.status is CandidateStatus.STORED
                    else candidate.mark_stored(processed_at=self._clock.now())
                )
                resolved_candidates.append(self._persist_candidates((stored,))[0])
                links.append(existing)
                continue
            if candidate.status is CandidateStatus.STORED:
                raise DatabaseError(
                    "A stored candidate is missing its final-memory provenance"
                )

            event_records = []
            for event_id in candidate.source_ids:
                event = events_by_id.get(event_id)
                if event is None:
                    raise DatabaseError(
                        "A memory candidate references an unavailable event"
                    )
                event_records.append(event)
            observation_ids = tuple(
                dict.fromkeys(
                    observation_id
                    for event in event_records
                    for observation_id in event.source_observation_ids
                )
            )
            appended = self._memory_store.append(
                candidate.text,
                idempotency_key=candidate.idempotency_key,
            )
            maintenance_required |= appended.maintenance_required
            link = MemoryLink(
                id=self._identifiers.new(),
                memory_backend_id=appended.backend_id,
                candidate_id=candidate.id,
                provenance=MemoryProvenance(
                    candidate_id=candidate.id,
                    event_ids=candidate.source_ids,
                    observation_ids=observation_ids,
                ),
                confidence=candidate.confidence,
                created_at=self._clock.now(),
            )
            stored = candidate.mark_stored(processed_at=self._clock.now())
            with session_scope(self._engine) as session:
                repository = PipelineRepository(session)
                persisted_link = repository.save_memory_link(link)
                persisted_candidate = repository.save_candidate(stored)
            links.append(persisted_link)
            resolved_candidates.append(persisted_candidate)
        return tuple(resolved_candidates), tuple(links), maintenance_required

    def _save_run(self, run: ProcessingRun) -> None:
        with session_scope(self._engine) as session:
            PipelineRepository(session).save_processing_run(run)


def _safe_error_code(error: Exception) -> str:
    if isinstance(error, CollectorUnavailableError):
        return "collector_unavailable"
    if isinstance(error, DatabaseError):
        return "database_error"
    if isinstance(error, MemoryStoreError):
        return "memory_store_error"
    if isinstance(error, PipelineError):
        return "pipeline_error"
    return "unexpected_pipeline_failure"
