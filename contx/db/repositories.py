"""Transaction-scoped repositories for the v0.0.1 pipeline."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from contx.db.models import (
    CandidateEventModel,
    EventModel,
    EventObservationModel,
    MemoryCandidateModel,
    MemoryLinkModel,
    ObservationModel,
    ProcessingRunModel,
)
from contx.errors import DatabaseError
from contx.models import (
    CandidateStatus,
    EpistemicStatus,
    Event,
    MemoryCandidate,
    MemoryLink,
    MemoryLinkStatus,
    MemoryProvenance,
    Observation,
    ObservationStatus,
    ProcessingRun,
    Sensitivity,
    SourceType,
)
from contx.models.common import format_utc, parse_utc


class PipelineRepository:
    """Persist pipeline records inside a caller-owned transaction."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def save_observation(self, observation: Observation) -> Observation:
        existing = self._session.scalar(
            select(ObservationModel).where(
                ObservationModel.idempotency_key == observation.idempotency_key
            )
        )
        if existing is not None:
            if existing.id == str(observation.id):
                _update_observation_state(existing, observation)
                self._session.flush()
                return observation
            return _observation_from_model(existing)
        self._session.add(_observation_to_model(observation))
        self._session.flush()
        return observation

    def save_event(self, event: Event) -> Event:
        existing = self._session.scalar(
            select(EventModel).where(
                EventModel.idempotency_key == event.idempotency_key
            )
        )
        if existing is not None:
            return _event_from_model(existing)
        self._require_ids(ObservationModel, event.source_observation_ids, "observation")
        self._session.add(_event_to_model(event))
        self._session.flush()
        self._session.add_all(
            EventObservationModel(
                event_id=str(event.id), observation_id=str(observation_id)
            )
            for observation_id in event.source_observation_ids
        )
        self._session.flush()
        return event

    def save_candidate(self, candidate: MemoryCandidate) -> MemoryCandidate:
        existing = self._session.scalar(
            select(MemoryCandidateModel).where(
                MemoryCandidateModel.idempotency_key == candidate.idempotency_key
            )
        )
        if existing is not None:
            if existing.id == str(candidate.id):
                _update_candidate_state(existing, candidate)
                self._session.flush()
                return candidate
            return _candidate_from_model(existing)
        self._require_ids(EventModel, candidate.source_ids, "event")
        self._session.add(_candidate_to_model(candidate))
        self._session.flush()
        self._session.add_all(
            CandidateEventModel(candidate_id=str(candidate.id), event_id=str(event_id))
            for event_id in candidate.source_ids
        )
        self._session.flush()
        return candidate

    def save_memory_link(self, link: MemoryLink) -> MemoryLink:
        existing = self._session.scalar(
            select(MemoryLinkModel).where(
                MemoryLinkModel.candidate_id == str(link.candidate_id)
            )
        )
        if existing is not None:
            return _memory_link_from_model(existing)
        self._validate_memory_provenance(link)
        self._session.add(_memory_link_to_model(link))
        self._session.flush()
        return link

    def memory_link_by_backend_id(self, backend_id: str) -> MemoryLink | None:
        """Load one typed, queryable provenance link."""
        model = self._session.scalar(
            select(MemoryLinkModel).where(
                MemoryLinkModel.memory_backend_id == backend_id
            )
        )
        return None if model is None else _memory_link_from_model(model)

    def save_processing_run(self, run: ProcessingRun) -> ProcessingRun:
        model = self._session.get(ProcessingRunModel, str(run.id))
        if model is None:
            self._session.add(_processing_run_to_model(run))
        else:
            _update_processing_run_model(model, run)
        self._session.flush()
        return run

    def count(
        self,
        model: type[ObservationModel]
        | type[EventModel]
        | type[MemoryCandidateModel]
        | type[MemoryLinkModel]
        | type[ProcessingRunModel],
    ) -> int:
        return int(self._session.scalar(select(func.count()).select_from(model)) or 0)

    def _require_ids(
        self,
        model: type[ObservationModel] | type[EventModel],
        identifiers: tuple[UUID, ...],
        label: str,
    ) -> None:
        found = set(
            self._session.scalars(
                select(model.id).where(model.id.in_(str(item) for item in identifiers))
            )
        )
        expected = {str(item) for item in identifiers}
        if found != expected:
            raise DatabaseError(
                f"Cannot persist provenance: a source {label} is missing"
            )

    def _validate_memory_provenance(self, link: MemoryLink) -> None:
        candidate = self._session.get(MemoryCandidateModel, str(link.candidate_id))
        if candidate is None or candidate.status not in {
            CandidateStatus.ACCEPTED.value,
            CandidateStatus.STORED.value,
        }:
            raise DatabaseError(
                "Cannot link memory to a missing or unaccepted candidate"
            )

        event_ids = set(
            self._session.scalars(
                select(CandidateEventModel.event_id).where(
                    CandidateEventModel.candidate_id == str(link.candidate_id)
                )
            )
        )
        expected_events = {str(item) for item in link.provenance.event_ids}
        if event_ids != expected_events:
            raise DatabaseError("Memory provenance does not match candidate events")

        observation_ids = set(
            self._session.scalars(
                select(EventObservationModel.observation_id).where(
                    EventObservationModel.event_id.in_(event_ids)
                )
            )
        )
        expected_observations = {str(item) for item in link.provenance.observation_ids}
        if observation_ids != expected_observations:
            raise DatabaseError("Memory provenance does not match event observations")


def _observation_to_model(record: Observation) -> ObservationModel:
    return ObservationModel(
        id=str(record.id),
        idempotency_key=record.idempotency_key,
        source_type=record.source_type.value,
        captured_at=format_utc(record.captured_at),
        started_at=None if record.started_at is None else format_utc(record.started_at),
        ended_at=None if record.ended_at is None else format_utc(record.ended_at),
        app_name=record.app_name,
        app_bundle_id=record.app_bundle_id,
        window_title=record.window_title,
        artifact_path=record.artifact_path,
        content_hash=record.content_hash,
        perceptual_hash=record.perceptual_hash,
        excluded=record.excluded,
        exclusion_reason=record.exclusion_reason,
        processing_status=record.processing_status.value,
        expires_at=None if record.expires_at is None else format_utc(record.expires_at),
        created_at=format_utc(record.created_at),
    )


def _observation_from_model(model: ObservationModel) -> Observation:
    return Observation(
        id=UUID(model.id),
        idempotency_key=model.idempotency_key,
        source_type=SourceType(model.source_type),
        captured_at=parse_utc(model.captured_at),
        started_at=None if model.started_at is None else parse_utc(model.started_at),
        ended_at=None if model.ended_at is None else parse_utc(model.ended_at),
        app_name=model.app_name,
        app_bundle_id=model.app_bundle_id,
        window_title=model.window_title,
        artifact_path=model.artifact_path,
        content_hash=model.content_hash,
        perceptual_hash=model.perceptual_hash,
        excluded=model.excluded,
        exclusion_reason=model.exclusion_reason,
        processing_status=ObservationStatus(model.processing_status),
        expires_at=None if model.expires_at is None else parse_utc(model.expires_at),
        created_at=parse_utc(model.created_at),
    )


def _update_observation_state(model: ObservationModel, record: Observation) -> None:
    current = ObservationStatus(model.processing_status)
    if current is record.processing_status:
        return
    if current is not ObservationStatus.COLLECTED:
        raise DatabaseError("Cannot change a finalized observation state")
    if record.processing_status not in {
        ObservationStatus.PROCESSED,
        ObservationStatus.REJECTED,
    }:
        raise DatabaseError("Invalid observation state transition")
    model.processing_status = record.processing_status.value


def _event_to_model(record: Event) -> EventModel:
    return EventModel(
        id=str(record.id),
        idempotency_key=record.idempotency_key,
        type=record.type,
        summary=record.summary,
        facts=record.facts,
        started_at=format_utc(record.started_at),
        ended_at=format_utc(record.ended_at),
        epistemic_status=record.epistemic_status.value,
        confidence=record.confidence,
        sensitivity=record.sensitivity.value,
        projects=list(record.projects),
        entities=list(record.entities),
        source_observation_ids=[str(item) for item in record.source_observation_ids],
        processing_version=record.processing_version,
        created_at=format_utc(record.created_at),
        updated_at=format_utc(record.updated_at),
    )


def _event_from_model(model: EventModel) -> Event:
    return Event(
        id=UUID(model.id),
        idempotency_key=model.idempotency_key,
        type=model.type,
        summary=model.summary,
        facts=model.facts,
        started_at=parse_utc(model.started_at),
        ended_at=parse_utc(model.ended_at),
        epistemic_status=EpistemicStatus(model.epistemic_status),
        confidence=model.confidence,
        sensitivity=Sensitivity(model.sensitivity),
        projects=tuple(model.projects),
        entities=tuple(model.entities),
        source_observation_ids=tuple(
            UUID(item) for item in model.source_observation_ids
        ),
        processing_version=model.processing_version,
        created_at=parse_utc(model.created_at),
        updated_at=parse_utc(model.updated_at),
    )


def _candidate_to_model(record: MemoryCandidate) -> MemoryCandidateModel:
    return MemoryCandidateModel(
        id=str(record.id),
        idempotency_key=record.idempotency_key,
        text=record.text,
        source_type=record.source_type,
        source_ids=[str(item) for item in record.source_ids],
        importance=record.importance,
        durability=record.durability,
        novelty=record.novelty,
        confidence=record.confidence,
        sensitivity=record.sensitivity.value,
        score=record.score,
        status=record.status.value,
        rejection_reason=record.rejection_reason,
        created_at=format_utc(record.created_at),
        processed_at=None
        if record.processed_at is None
        else format_utc(record.processed_at),
    )


def _candidate_from_model(model: MemoryCandidateModel) -> MemoryCandidate:
    return MemoryCandidate(
        id=UUID(model.id),
        idempotency_key=model.idempotency_key,
        text=model.text,
        source_type=model.source_type,
        source_ids=tuple(UUID(item) for item in model.source_ids),
        importance=model.importance,
        durability=model.durability,
        novelty=model.novelty,
        confidence=model.confidence,
        sensitivity=Sensitivity(model.sensitivity),
        score=model.score,
        status=CandidateStatus(model.status),
        rejection_reason=model.rejection_reason,
        created_at=parse_utc(model.created_at),
        processed_at=None
        if model.processed_at is None
        else parse_utc(model.processed_at),
    )


def _update_candidate_state(
    model: MemoryCandidateModel, record: MemoryCandidate
) -> None:
    current = CandidateStatus(model.status)
    if current is record.status:
        return
    allowed = (
        current is CandidateStatus.PENDING
        and record.status
        in {
            CandidateStatus.ACCEPTED,
            CandidateStatus.REJECTED,
            CandidateStatus.DEFERRED,
        }
    ) or (
        current is CandidateStatus.ACCEPTED and record.status is CandidateStatus.STORED
    )
    if not allowed:
        raise DatabaseError("Invalid candidate state transition")
    model.status = record.status.value
    model.rejection_reason = record.rejection_reason
    model.processed_at = (
        None if record.processed_at is None else format_utc(record.processed_at)
    )


def _memory_link_to_model(record: MemoryLink) -> MemoryLinkModel:
    return MemoryLinkModel(
        id=str(record.id),
        memory_backend_id=record.memory_backend_id,
        candidate_id=str(record.candidate_id),
        provenance=record.provenance.model_dump(mode="json"),
        confidence=record.confidence,
        status=record.status.value,
        supersedes_memory_id=None
        if record.supersedes_memory_id is None
        else str(record.supersedes_memory_id),
        created_at=format_utc(record.created_at),
    )


def _memory_link_from_model(model: MemoryLinkModel) -> MemoryLink:
    provenance_data = model.provenance
    return MemoryLink(
        id=UUID(model.id),
        memory_backend_id=model.memory_backend_id,
        candidate_id=UUID(model.candidate_id),
        provenance=MemoryProvenance(
            candidate_id=UUID(str(provenance_data["candidate_id"])),
            event_ids=tuple(UUID(item) for item in provenance_data["event_ids"]),
            observation_ids=tuple(
                UUID(item) for item in provenance_data["observation_ids"]
            ),
        ),
        confidence=model.confidence,
        status=MemoryLinkStatus(model.status),
        supersedes_memory_id=None
        if model.supersedes_memory_id is None
        else UUID(model.supersedes_memory_id),
        created_at=parse_utc(model.created_at),
    )


def _processing_run_to_model(record: ProcessingRun) -> ProcessingRunModel:
    model = ProcessingRunModel(id=str(record.id))
    _update_processing_run_model(model, record)
    return model


def _update_processing_run_model(
    model: ProcessingRunModel, record: ProcessingRun
) -> None:
    model.pipeline = record.pipeline
    model.version = record.version
    model.started_at = format_utc(record.started_at)
    model.ended_at = None if record.ended_at is None else format_utc(record.ended_at)
    model.status = record.status.value
    model.input_count = record.input_count
    model.output_count = record.output_count
    model.error_code = record.error_code
    model.error_summary = record.error_summary
