"""Transaction-scoped repositories for the v0.0.1 pipeline."""

from __future__ import annotations

import json
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from contx.db.models import (
    AgentProposalAdoptionBuildModel,
    AgentProposalModel,
    CandidateBuildModel,
    CandidateDecisionModel,
    CandidateEvaluationBuildModel,
    CandidateEventModel,
    CandidatePatternModel,
    CandidateProcessingRunModel,
    CollectionControlModel,
    EventCorrectionModel,
    EventModel,
    EventModelTransformationModel,
    EventObservationModel,
    EventProcessingRunModel,
    ExclusionRuleModel,
    MemoryCandidateModel,
    MemoryCorrectionBuildModel,
    MemoryLinkModel,
    MemoryLinkProcessingRunModel,
    MemoryPromotionBuildModel,
    ModelAttemptModel,
    ModelTransformationModel,
    ModelTransformationObservationModel,
    ModelTransformationRunModel,
    ObservationModel,
    PatternBuildModel,
    PatternEventModel,
    PatternModel,
    PatternProcessingRunModel,
    ProcessingRunModel,
    TimelineBuildModel,
)
from contx.errors import DatabaseError
from contx.model_provider import (
    ModelAttempt,
    ModelAttemptInvocation,
    ModelInterpretation,
    ModelTransformation,
    ModelTransformationStatus,
)
from contx.models import (
    ActivityState,
    AgentProposal,
    AgentProposalAdoptionBuild,
    AgentProposalDecision,
    AgentProposalReasonCode,
    AgentProposalStatus,
    AgentProposalType,
    AgentRole,
    CandidateBuild,
    CandidateDecision,
    CandidateDecisionStatus,
    CandidateEvaluationBuild,
    CandidateStatus,
    CollectionControl,
    EpistemicStatus,
    Event,
    EventCorrection,
    EventCorrectionContent,
    EventType,
    ExclusionRule,
    ExclusionRuleType,
    ExclusionScope,
    MemoryCandidate,
    MemoryCorrectionBuild,
    MemoryLink,
    MemoryLinkStatus,
    MemoryPromotionBuild,
    MemoryProvenance,
    Observation,
    ObservationStatus,
    Pattern,
    PatternBuild,
    PatternStatus,
    PatternType,
    ProcessingRun,
    ProcessingRunStatus,
    ProposalReferenceType,
    Sensitivity,
    SourceType,
    TimelineBuild,
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
            if existing.processing_status == ObservationStatus.PURGED.value:
                return _observation_from_model(existing)
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
                persisted = _candidate_from_model(existing)
                if _candidate_identity(persisted) != _candidate_identity(candidate):
                    raise DatabaseError("Memory candidate identity conflicts")
                _update_candidate_state(existing, candidate)
                self._session.flush()
                return candidate
            return _candidate_from_model(existing)
        source_model: (
            type[EventModel]
            | type[PatternModel]
            | type[MemoryLinkModel]
            | type[AgentProposalModel]
        )
        if candidate.source_type == "pattern":
            source_model = PatternModel
            source_label = "pattern"
        elif candidate.source_type == "memory_correction":
            source_model = MemoryLinkModel
            source_label = "memory"
        elif candidate.source_type == "agent_proposal":
            source_model = AgentProposalModel
            source_label = "agent proposal"
        else:
            source_model = EventModel
            source_label = "event"
        self._require_ids(
            source_model,
            candidate.source_ids,
            source_label,
        )
        self._session.add(_candidate_to_model(candidate))
        self._session.flush()
        if candidate.source_type == "pattern":
            self._session.add_all(
                CandidatePatternModel(
                    candidate_id=str(candidate.id),
                    pattern_id=str(pattern_id),
                )
                for pattern_id in candidate.source_ids
            )
        elif candidate.source_type not in {"memory_correction", "agent_proposal"}:
            self._session.add_all(
                CandidateEventModel(
                    candidate_id=str(candidate.id),
                    event_id=str(event_id),
                )
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

    def memory_link_by_candidate_id(self, candidate_id: UUID) -> MemoryLink | None:
        """Load the final-memory link for one accepted candidate."""
        model = self._session.scalar(
            select(MemoryLinkModel).where(
                MemoryLinkModel.candidate_id == str(candidate_id)
            )
        )
        return None if model is None else _memory_link_from_model(model)

    def memory_link_by_id(self, memory_link_id: UUID) -> MemoryLink | None:
        model = self._session.get(MemoryLinkModel, str(memory_link_id))
        return None if model is None else _memory_link_from_model(model)

    def memory_link_superseding(self, memory_link_id: UUID) -> MemoryLink | None:
        model = self._session.scalar(
            select(MemoryLinkModel).where(
                MemoryLinkModel.supersedes_memory_id == str(memory_link_id)
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

    def observation_by_id(self, observation_id: UUID) -> Observation | None:
        model = self._session.get(ObservationModel, str(observation_id))
        return None if model is None else _observation_from_model(model)

    def processing_run_by_id(self, run_id: UUID) -> ProcessingRun | None:
        model = self._session.get(ProcessingRunModel, str(run_id))
        return None if model is None else _processing_run_from_model(model)

    def event_by_id(self, event_id: UUID) -> Event | None:
        model = self._session.get(EventModel, str(event_id))
        return None if model is None else _event_from_model(model)

    def candidate_by_id(self, candidate_id: UUID) -> MemoryCandidate | None:
        model = self._session.get(MemoryCandidateModel, str(candidate_id))
        return None if model is None else _candidate_from_model(model)

    def active_memory_candidates(self) -> tuple[MemoryCandidate, ...]:
        """Load active final-memory text for proposal duplicate checks."""
        models = self._session.scalars(
            select(MemoryCandidateModel)
            .join(
                MemoryLinkModel,
                MemoryLinkModel.candidate_id == MemoryCandidateModel.id,
            )
            .where(MemoryLinkModel.status == MemoryLinkStatus.ACTIVE.value)
            .order_by(MemoryLinkModel.created_at.desc(), MemoryLinkModel.id.desc())
        )
        return tuple(_candidate_from_model(model) for model in models)

    def active_memory_records(
        self,
    ) -> tuple[tuple[MemoryLink, MemoryCandidate], ...]:
        """Load the exact active-memory set in stable chronological order."""
        rows = self._session.execute(
            select(MemoryLinkModel, MemoryCandidateModel)
            .join(
                MemoryCandidateModel,
                MemoryCandidateModel.id == MemoryLinkModel.candidate_id,
            )
            .where(MemoryLinkModel.status == MemoryLinkStatus.ACTIVE.value)
            .order_by(MemoryLinkModel.created_at, MemoryLinkModel.id)
        )
        return tuple(
            (_memory_link_from_model(link), _candidate_from_model(candidate))
            for link, candidate in rows
        )

    def observations(self, *, limit: int = 100) -> tuple[Observation, ...]:
        """Load recent observation metadata for local inspection."""
        _validate_inspection_limit(limit)
        models = self._session.scalars(
            select(ObservationModel)
            .order_by(ObservationModel.captured_at.desc(), ObservationModel.id.desc())
            .limit(limit)
        )
        return tuple(_observation_from_model(model) for model in models)

    def events(self, *, limit: int = 100) -> tuple[Event, ...]:
        """Load recent semantic events in reverse chronological order."""
        _validate_inspection_limit(limit)
        models = self._session.scalars(
            select(EventModel)
            .order_by(EventModel.started_at.desc(), EventModel.id.desc())
            .limit(limit)
        )
        return tuple(_event_from_model(model) for model in models)

    def candidates(self, *, limit: int = 100) -> tuple[MemoryCandidate, ...]:
        """Load recent memory candidates regardless of decision state."""
        _validate_inspection_limit(limit)
        models = self._session.scalars(
            select(MemoryCandidateModel)
            .order_by(
                MemoryCandidateModel.created_at.desc(),
                MemoryCandidateModel.id.desc(),
            )
            .limit(limit)
        )
        return tuple(_candidate_from_model(model) for model in models)

    def memory_records(
        self, *, limit: int = 100
    ) -> tuple[tuple[MemoryLink, MemoryCandidate], ...]:
        """Load recent final-memory links with their exact candidate text."""
        _validate_inspection_limit(limit)
        rows = self._session.execute(
            select(MemoryLinkModel, MemoryCandidateModel)
            .join(
                MemoryCandidateModel,
                MemoryCandidateModel.id == MemoryLinkModel.candidate_id,
            )
            .order_by(MemoryLinkModel.created_at.desc(), MemoryLinkModel.id.desc())
            .limit(limit)
        )
        return tuple(
            (_memory_link_from_model(link), _candidate_from_model(candidate))
            for link, candidate in rows
        )

    def durable_memory_records_since(
        self,
        since: datetime,
    ) -> tuple[tuple[MemoryLink, MemoryCandidate], ...]:
        """Load every finalized memory created at or after one pilot boundary."""
        rows = self._session.execute(
            select(MemoryLinkModel, MemoryCandidateModel)
            .join(
                MemoryCandidateModel,
                MemoryCandidateModel.id == MemoryLinkModel.candidate_id,
            )
            .where(
                MemoryLinkModel.created_at >= format_utc(since),
                MemoryLinkModel.status != MemoryLinkStatus.PENDING.value,
                MemoryCandidateModel.status == CandidateStatus.STORED.value,
            )
            .order_by(MemoryLinkModel.created_at, MemoryLinkModel.id)
        )
        return tuple(
            (_memory_link_from_model(link), _candidate_from_model(candidate))
            for link, candidate in rows
        )

    def processing_runs(self, *, limit: int = 100) -> tuple[ProcessingRun, ...]:
        """Load recent observable pipeline outcomes."""
        _validate_inspection_limit(limit)
        models = self._session.scalars(
            select(ProcessingRunModel)
            .order_by(
                ProcessingRunModel.started_at.desc(), ProcessingRunModel.id.desc()
            )
            .limit(limit)
        )
        return tuple(_processing_run_from_model(model) for model in models)

    def count(
        self,
        model: type[ObservationModel]
        | type[EventModel]
        | type[PatternModel]
        | type[MemoryCandidateModel]
        | type[CandidateDecisionModel]
        | type[MemoryLinkModel]
        | type[ProcessingRunModel],
    ) -> int:
        return int(self._session.scalar(select(func.count()).select_from(model)) or 0)

    def record_counts(self) -> dict[str, int]:
        """Return bounded-label totals for the local status surface."""
        return {
            "observations": self.count(ObservationModel),
            "events": self.count(EventModel),
            "patterns": self.count(PatternModel),
            "candidates": self.count(MemoryCandidateModel),
            "memories": self.count(MemoryLinkModel),
            "processing_runs": self.count(ProcessingRunModel),
        }

    def _require_ids(
        self,
        model: (
            type[ObservationModel]
            | type[EventModel]
            | type[PatternModel]
            | type[MemoryLinkModel]
            | type[AgentProposalModel]
        ),
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
        if candidate is None:
            raise DatabaseError("Cannot link memory to a missing candidate")
        if link.candidate_decision_id is None:
            if candidate.status not in {
                CandidateStatus.ACCEPTED.value,
                CandidateStatus.STORED.value,
            }:
                raise DatabaseError("Cannot link memory to an unaccepted candidate")
        else:
            decision = self._session.get(
                CandidateDecisionModel,
                str(link.candidate_decision_id),
            )
            if (
                decision is None
                or decision.candidate_id != str(link.candidate_id)
                or decision.status != CandidateDecisionStatus.ACCEPTED.value
            ):
                raise DatabaseError("Memory promotion decision is missing or invalid")

        expected_patterns = {str(item) for item in link.provenance.pattern_ids}
        if candidate.source_type == "memory_correction":
            event_ids = self._validate_correction_provenance(
                link,
                candidate=candidate,
            )
        elif candidate.source_type == "agent_proposal":
            event_ids = self._validate_agent_proposal_provenance(
                link,
                candidate=candidate,
            )
        elif candidate.source_type == "pattern":
            pattern_ids = set(
                self._session.scalars(
                    select(CandidatePatternModel.pattern_id).where(
                        CandidatePatternModel.candidate_id == str(link.candidate_id)
                    )
                )
            )
            if pattern_ids != expected_patterns:
                raise DatabaseError(
                    "Memory provenance does not match candidate patterns"
                )
            event_ids = set(
                self._session.scalars(
                    select(PatternEventModel.event_id).where(
                        PatternEventModel.pattern_id.in_(pattern_ids)
                    )
                )
            )
        else:
            if expected_patterns:
                raise DatabaseError("Event memory cannot cite pattern provenance")
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

    def _validate_correction_provenance(
        self,
        link: MemoryLink,
        *,
        candidate: MemoryCandidateModel,
    ) -> set[str]:
        if link.candidate_decision_id is not None:
            raise DatabaseError("Explicit memory corrections have no worker decision")
        if (
            len(candidate.source_ids) != 1
            or link.supersedes_memory_id is None
            or candidate.source_ids[0] != str(link.supersedes_memory_id)
        ):
            raise DatabaseError("Memory correction target is inconsistent")
        target = self._session.get(
            MemoryLinkModel,
            str(link.supersedes_memory_id),
        )
        if target is None or target.status != MemoryLinkStatus.ACTIVE.value:
            raise DatabaseError("Memory correction target is not active")
        target_candidate = self._session.get(
            MemoryCandidateModel,
            target.candidate_id,
        )
        if (
            target_candidate is None
            or target_candidate.sensitivity != candidate.sensitivity
        ):
            raise DatabaseError("Memory correction sensitivity is inconsistent")
        target_provenance = target.provenance
        for field in ("pattern_ids", "event_ids", "observation_ids"):
            if set(link.provenance.model_dump(mode="json")[field]) != set(
                target_provenance[field]
            ):
                raise DatabaseError("Memory correction provenance is incomplete")
        return {str(item) for item in target_provenance["event_ids"]}

    def _validate_agent_proposal_provenance(
        self,
        link: MemoryLink,
        *,
        candidate: MemoryCandidateModel,
    ) -> set[str]:
        if (
            link.candidate_decision_id is not None
            or link.supersedes_memory_id is not None
            or len(candidate.source_ids) != 1
        ):
            raise DatabaseError("Agent proposal memory identity is inconsistent")
        proposal_id = candidate.source_ids[0]
        proposal = self._session.get(AgentProposalModel, proposal_id)
        build = self._session.get(AgentProposalAdoptionBuildModel, proposal_id)
        if (
            proposal is None
            or proposal.status != AgentProposalStatus.PENDING.value
            or proposal.agent_role != AgentRole.PRIMARY.value
            or proposal.text != candidate.text
            or proposal.reference_type is None
            or proposal.reference_id is None
            or build is None
            or build.candidate_id != candidate.id
            or build.decision != AgentProposalDecision.ACCEPTED.value
        ):
            raise DatabaseError("Agent proposal adoption audit is missing or invalid")
        expected_patterns = {str(item) for item in link.provenance.pattern_ids}
        if proposal.reference_type == ProposalReferenceType.EVENT.value:
            if expected_patterns:
                raise DatabaseError("Event proposal cannot cite pattern provenance")
            event = self._session.get(EventModel, proposal.reference_id)
            if event is None or event.sensitivity != candidate.sensitivity:
                raise DatabaseError("Agent proposal event sensitivity is inconsistent")
            event_ids = {proposal.reference_id}
        elif proposal.reference_type == ProposalReferenceType.PATTERN.value:
            if expected_patterns != {proposal.reference_id}:
                raise DatabaseError(
                    "Agent proposal provenance does not match its pattern"
                )
            pattern = self._session.get(PatternModel, proposal.reference_id)
            if pattern is None or pattern.sensitivity != candidate.sensitivity:
                raise DatabaseError(
                    "Agent proposal pattern sensitivity is inconsistent"
                )
            event_ids = set(
                self._session.scalars(
                    select(PatternEventModel.event_id).where(
                        PatternEventModel.pattern_id == proposal.reference_id
                    )
                )
            )
        elif proposal.reference_type == ProposalReferenceType.MEMORY.value:
            reference = self._session.get(MemoryLinkModel, proposal.reference_id)
            if reference is None or reference.status != MemoryLinkStatus.ACTIVE.value:
                raise DatabaseError("Agent proposal memory reference is not active")
            reference_candidate = self._session.get(
                MemoryCandidateModel,
                reference.candidate_id,
            )
            if (
                reference_candidate is None
                or reference_candidate.sensitivity != candidate.sensitivity
            ):
                raise DatabaseError("Agent proposal memory sensitivity is inconsistent")
            reference_provenance = reference.provenance
            if expected_patterns != set(reference_provenance["pattern_ids"]):
                raise DatabaseError(
                    "Agent proposal provenance does not match referenced memory"
                )
            event_ids = set(reference_provenance["event_ids"])
        else:
            raise DatabaseError("Agent proposal reference type is invalid")
        return {str(item) for item in event_ids}


class ModelTransformationRepository:
    """Persist replayable local-model work with source and run provenance."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def save(
        self,
        transformation: ModelTransformation,
        *,
        processing_run_id: UUID | None = None,
    ) -> ModelTransformation:
        existing_model = self._session.scalar(
            select(ModelTransformationModel).where(
                ModelTransformationModel.idempotency_key
                == transformation.idempotency_key
            )
        )
        if existing_model is None:
            self._validate_sources(transformation)
            self._session.add(_model_transformation_to_model(transformation))
            self._session.flush()
            self._session.add_all(
                ModelTransformationObservationModel(
                    transformation_id=str(transformation.id),
                    observation_id=str(observation_id),
                )
                for observation_id in transformation.source_observation_ids
            )
            persisted = transformation
        else:
            existing = _model_transformation_from_model(existing_model)
            if existing.id != transformation.id:
                _require_same_transformation_identity(existing, transformation)
                return existing
            _validate_transformation_transition(existing, transformation)
            _update_model_transformation(existing_model, transformation)
            persisted = transformation

        if processing_run_id is not None:
            self._link_processing_run(persisted.id, processing_run_id)
        elif persisted.status is not ModelTransformationStatus.PENDING:
            raise DatabaseError(
                "Non-pending model transformation requires a processing run"
            )
        self._session.flush()
        return persisted

    def by_id(self, transformation_id: UUID) -> ModelTransformation | None:
        model = self._session.get(ModelTransformationModel, str(transformation_id))
        return None if model is None else _model_transformation_from_model(model)

    def save_attempt(self, attempt: ModelAttempt) -> ModelAttempt:
        """Persist one immutable terminal attempt outcome idempotently."""
        existing_model = self._session.get(
            ModelAttemptModel,
            (str(attempt.transformation_id), attempt.attempt_number),
        )
        if existing_model is not None:
            existing = _model_attempt_from_model(existing_model)
            if existing != attempt:
                raise DatabaseError("Model attempt identity conflicts")
            return existing
        transformation = self._session.get(
            ModelTransformationModel, str(attempt.transformation_id)
        )
        run = self._session.get(ProcessingRunModel, str(attempt.processing_run_id))
        linked_run = self._session.get(
            ModelTransformationRunModel,
            (str(attempt.transformation_id), str(attempt.processing_run_id)),
        )
        if transformation is None or run is None or linked_run is None:
            raise DatabaseError("Model attempt provenance is incomplete")
        if transformation.attempt_count < attempt.attempt_number:
            raise DatabaseError("Model attempt exceeds transformation state")
        self._session.add(_model_attempt_to_model(attempt))
        self._session.flush()
        return attempt

    def attempts(self, *, since: datetime | None = None) -> tuple[ModelAttempt, ...]:
        """Load complete content-free attempt history in stable order."""
        statement = select(ModelAttemptModel).order_by(
            ModelAttemptModel.started_at,
            ModelAttemptModel.transformation_id,
            ModelAttemptModel.attempt_number,
        )
        if since is not None:
            statement = statement.where(
                ModelAttemptModel.started_at >= format_utc(since)
            )
        return tuple(
            _model_attempt_from_model(model)
            for model in self._session.scalars(statement)
        )

    def by_idempotency_key(self, key: str) -> ModelTransformation | None:
        model = self._session.scalar(
            select(ModelTransformationModel).where(
                ModelTransformationModel.idempotency_key == key
            )
        )
        return None if model is None else _model_transformation_from_model(model)

    def due(
        self,
        *,
        at: datetime,
        limit: int = 10,
        provider: str | None = None,
        endpoint: str | None = None,
        configured_model: str | None = None,
        prompt_version: str | None = None,
        output_schema_version: str | None = None,
    ) -> tuple[ModelTransformation, ...]:
        if limit < 1:
            raise ValueError("model transformation limit must be positive")
        configuration = _optional_model_configuration(
            provider,
            endpoint,
            configured_model,
            prompt_version,
            output_schema_version,
        )
        statement = (
            select(ModelTransformationModel)
            .where(
                ModelTransformationModel.status.in_(
                    (
                        ModelTransformationStatus.PENDING.value,
                        ModelTransformationStatus.FAILED.value,
                    )
                ),
                ModelTransformationModel.next_attempt_at <= format_utc(at),
            )
            .order_by(
                ModelTransformationModel.next_attempt_at,
                ModelTransformationModel.created_at,
                ModelTransformationModel.id,
            )
            .limit(limit)
        )
        if configuration is not None:
            statement = statement.where(
                ModelTransformationModel.provider == configuration[0],
                ModelTransformationModel.endpoint == configuration[1],
                ModelTransformationModel.configured_model == configuration[2],
                ModelTransformationModel.prompt_version == configuration[3],
                ModelTransformationModel.output_schema_version == configuration[4],
            )
        models = self._session.scalars(statement)
        return tuple(_model_transformation_from_model(model) for model in models)

    def unqueued_screenshots(
        self,
        *,
        at: datetime,
        limit: int = 1000,
        provider: str | None = None,
        endpoint: str | None = None,
        configured_model: str | None = None,
        prompt_version: str | None = None,
        output_schema_version: str | None = None,
    ) -> tuple[Observation, ...]:
        if limit < 1:
            raise ValueError("unqueued screenshot limit must be positive")
        configuration = _optional_model_configuration(
            provider,
            endpoint,
            configured_model,
            prompt_version,
            output_schema_version,
        )
        linked_statement = (
            select(ModelTransformationObservationModel.observation_id)
            .join(
                ModelTransformationModel,
                ModelTransformationModel.id
                == ModelTransformationObservationModel.transformation_id,
            )
            .where(
                ModelTransformationObservationModel.observation_id
                == ObservationModel.id
            )
        )
        if configuration is not None:
            linked_statement = linked_statement.where(
                ModelTransformationModel.provider == configuration[0],
                ModelTransformationModel.endpoint == configuration[1],
                ModelTransformationModel.configured_model == configuration[2],
                ModelTransformationModel.prompt_version == configuration[3],
                ModelTransformationModel.output_schema_version == configuration[4],
            )
        linked = linked_statement.exists()
        models = self._session.scalars(
            select(ObservationModel)
            .where(
                ObservationModel.source_type == SourceType.SCREENSHOT.value,
                ObservationModel.processing_status == ObservationStatus.COLLECTED.value,
                ObservationModel.excluded.is_(False),
                ObservationModel.artifact_path.is_not(None),
                ObservationModel.content_hash.is_not(None),
                ObservationModel.expires_at > format_utc(at),
                ~linked,
            )
            .order_by(ObservationModel.captured_at, ObservationModel.id)
            .limit(limit)
        )
        return tuple(_observation_from_model(model) for model in models)

    def unqueued_screenshot_count(
        self,
        *,
        at: datetime,
        provider: str | None = None,
        endpoint: str | None = None,
        configured_model: str | None = None,
        prompt_version: str | None = None,
        output_schema_version: str | None = None,
    ) -> int:
        configuration = _optional_model_configuration(
            provider,
            endpoint,
            configured_model,
            prompt_version,
            output_schema_version,
        )
        linked_statement = (
            select(ModelTransformationObservationModel.observation_id)
            .join(
                ModelTransformationModel,
                ModelTransformationModel.id
                == ModelTransformationObservationModel.transformation_id,
            )
            .where(
                ModelTransformationObservationModel.observation_id
                == ObservationModel.id
            )
        )
        if configuration is not None:
            linked_statement = linked_statement.where(
                ModelTransformationModel.provider == configuration[0],
                ModelTransformationModel.endpoint == configuration[1],
                ModelTransformationModel.configured_model == configuration[2],
                ModelTransformationModel.prompt_version == configuration[3],
                ModelTransformationModel.output_schema_version == configuration[4],
            )
        linked = linked_statement.exists()
        return int(
            self._session.scalar(
                select(func.count())
                .select_from(ObservationModel)
                .where(
                    ObservationModel.source_type == SourceType.SCREENSHOT.value,
                    ObservationModel.processing_status
                    == ObservationStatus.COLLECTED.value,
                    ObservationModel.excluded.is_(False),
                    ObservationModel.artifact_path.is_not(None),
                    ObservationModel.content_hash.is_not(None),
                    ObservationModel.expires_at > format_utc(at),
                    ~linked,
                )
            )
            or 0
        )

    def source_observations(
        self,
        transformation: ModelTransformation,
    ) -> tuple[Observation, ...]:
        models = tuple(
            self._session.scalars(
                select(ObservationModel).where(
                    ObservationModel.id.in_(
                        str(value) for value in transformation.source_observation_ids
                    )
                )
            )
        )
        by_id = {UUID(model.id): _observation_from_model(model) for model in models}
        try:
            return tuple(
                by_id[value] for value in transformation.source_observation_ids
            )
        except KeyError as error:
            raise DatabaseError(
                "A model transformation source observation is unavailable"
            ) from error

    def succeeded(self, *, limit: int = 10000) -> tuple[ModelTransformation, ...]:
        if limit < 1:
            raise ValueError("successful model transformation limit must be positive")
        models = self._session.scalars(
            select(ModelTransformationModel)
            .where(
                ModelTransformationModel.status
                == ModelTransformationStatus.SUCCEEDED.value
            )
            .order_by(
                ModelTransformationModel.ended_at,
                ModelTransformationModel.id,
            )
            .limit(limit)
        )
        return tuple(_model_transformation_from_model(model) for model in models)

    def stale_running(
        self,
        *,
        before: datetime,
        limit: int = 1000,
    ) -> tuple[ModelTransformation, ...]:
        if limit < 1:
            raise ValueError("stale model transformation limit must be positive")
        models = self._session.scalars(
            select(ModelTransformationModel)
            .where(
                ModelTransformationModel.status
                == ModelTransformationStatus.RUNNING.value,
                ModelTransformationModel.started_at <= format_utc(before),
            )
            .order_by(
                ModelTransformationModel.started_at,
                ModelTransformationModel.id,
            )
            .limit(limit)
        )
        return tuple(_model_transformation_from_model(model) for model in models)

    def backlog_count(
        self,
        *,
        provider: str | None = None,
        endpoint: str | None = None,
        configured_model: str | None = None,
        prompt_version: str | None = None,
        output_schema_version: str | None = None,
    ) -> int:
        configuration = _optional_model_configuration(
            provider,
            endpoint,
            configured_model,
            prompt_version,
            output_schema_version,
        )
        statement = (
            select(func.count())
            .select_from(ModelTransformationModel)
            .where(
                ModelTransformationModel.status.in_(
                    (
                        ModelTransformationStatus.PENDING.value,
                        ModelTransformationStatus.RUNNING.value,
                        ModelTransformationStatus.FAILED.value,
                    )
                )
            )
        )
        if configuration is not None:
            statement = statement.where(
                ModelTransformationModel.provider == configuration[0],
                ModelTransformationModel.endpoint == configuration[1],
                ModelTransformationModel.configured_model == configuration[2],
                ModelTransformationModel.prompt_version == configuration[3],
                ModelTransformationModel.output_schema_version == configuration[4],
            )
        return int(self._session.scalar(statement) or 0)

    def abandoned_count(self) -> int:
        return int(
            self._session.scalar(
                select(func.count())
                .select_from(ModelTransformationModel)
                .where(
                    ModelTransformationModel.status
                    == ModelTransformationStatus.ABANDONED.value
                )
            )
            or 0
        )

    def processing_run_ids(self, transformation_id: UUID) -> tuple[UUID, ...]:
        return tuple(
            UUID(value)
            for value in self._session.scalars(
                select(ModelTransformationRunModel.processing_run_id)
                .where(
                    ModelTransformationRunModel.transformation_id
                    == str(transformation_id)
                )
                .order_by(ModelTransformationRunModel.processing_run_id)
            )
        )

    def count(self) -> int:
        return int(
            self._session.scalar(
                select(func.count()).select_from(ModelTransformationModel)
            )
            or 0
        )

    def list(self, *, limit: int = 100) -> tuple[ModelTransformation, ...]:
        """Load recent local-model transformations for privacy inspection."""
        _validate_inspection_limit(limit)
        models = self._session.scalars(
            select(ModelTransformationModel)
            .order_by(
                ModelTransformationModel.created_at.desc(),
                ModelTransformationModel.id.desc(),
            )
            .limit(limit)
        )
        return tuple(_model_transformation_from_model(model) for model in models)

    def _validate_sources(self, transformation: ModelTransformation) -> None:
        expected = {str(item) for item in transformation.source_observation_ids}
        observations = tuple(
            self._session.scalars(
                select(ObservationModel).where(ObservationModel.id.in_(expected))
            )
        )
        if {item.id for item in observations} != expected:
            raise DatabaseError(
                "Cannot persist model provenance: a source observation is missing"
            )
        if any(
            item.excluded
            or item.processing_status
            in {
                ObservationStatus.REJECTED.value,
                ObservationStatus.PURGED.value,
            }
            for item in observations
        ):
            raise DatabaseError(
                "Cannot persist model work for an excluded or unavailable source"
            )
        screenshots = tuple(
            item
            for item in observations
            if item.source_type == SourceType.SCREENSHOT.value
        )
        if (
            len(screenshots) != 1
            or screenshots[0].artifact_path is None
            or screenshots[0].content_hash != transformation.image_sha256
        ):
            raise DatabaseError(
                "Model transformation requires one matching screenshot source"
            )

    def _link_processing_run(
        self,
        transformation_id: UUID,
        processing_run_id: UUID,
    ) -> None:
        if self._session.get(ProcessingRunModel, str(processing_run_id)) is None:
            raise DatabaseError("Cannot link a missing model processing run")
        identity = {
            "transformation_id": str(transformation_id),
            "processing_run_id": str(processing_run_id),
        }
        if self._session.get(ModelTransformationRunModel, identity) is None:
            self._session.add(ModelTransformationRunModel(**identity))


class ModelEventRepository:
    """Persist model-derived events with transformation and run provenance."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def pending_transformations(
        self,
        *,
        processing_version: str,
        limit: int = 100,
    ) -> tuple[ModelTransformation, ...]:
        if limit < 1:
            raise ValueError("model event batch size must be positive")
        linked = (
            select(EventModelTransformationModel.transformation_id)
            .join(EventModel, EventModel.id == EventModelTransformationModel.event_id)
            .where(
                EventModelTransformationModel.transformation_id
                == ModelTransformationModel.id,
                EventModel.processing_version == processing_version,
            )
            .exists()
        )
        models = self._session.scalars(
            select(ModelTransformationModel)
            .where(
                ModelTransformationModel.status
                == ModelTransformationStatus.SUCCEEDED.value,
                ~linked,
            )
            .order_by(
                ModelTransformationModel.ended_at,
                ModelTransformationModel.id,
            )
            .limit(limit)
        )
        return tuple(_model_transformation_from_model(model) for model in models)

    def pending_count(self, *, processing_version: str) -> int:
        linked = (
            select(EventModelTransformationModel.transformation_id)
            .join(EventModel, EventModel.id == EventModelTransformationModel.event_id)
            .where(
                EventModelTransformationModel.transformation_id
                == ModelTransformationModel.id,
                EventModel.processing_version == processing_version,
            )
            .exists()
        )
        return int(
            self._session.scalar(
                select(func.count())
                .select_from(ModelTransformationModel)
                .where(
                    ModelTransformationModel.status
                    == ModelTransformationStatus.SUCCEEDED.value,
                    ~linked,
                )
            )
            or 0
        )

    def save(
        self,
        event: Event,
        *,
        transformation_ids: tuple[UUID, ...],
        processing_run_id: UUID,
    ) -> Event:
        if not transformation_ids or len(set(transformation_ids)) != len(
            transformation_ids
        ):
            raise DatabaseError(
                "A model event requires unique transformation provenance"
            )
        expected = {str(value) for value in transformation_ids}
        models = tuple(
            self._session.scalars(
                select(ModelTransformationModel).where(
                    ModelTransformationModel.id.in_(expected)
                )
            )
        )
        if {model.id for model in models} != expected:
            raise DatabaseError("A model event transformation is missing")
        transformations = tuple(
            _model_transformation_from_model(model) for model in models
        )
        if any(
            transformation.status is not ModelTransformationStatus.SUCCEEDED
            for transformation in transformations
        ):
            raise DatabaseError("A model event requires successful transformations")
        source_ids = {
            observation_id
            for transformation in transformations
            for observation_id in transformation.source_observation_ids
        }
        if source_ids != set(event.source_observation_ids):
            raise DatabaseError(
                "Model event observations do not match transformation provenance"
            )
        if self._session.get(ProcessingRunModel, str(processing_run_id)) is None:
            raise DatabaseError("A model event processing run is missing")

        persisted = PipelineRepository(self._session).save_event(event)
        if persisted.id != event.id:
            raise DatabaseError("Model event idempotency identity conflicts")
        self._session.add_all(
            EventModelTransformationModel(
                event_id=str(event.id),
                transformation_id=str(transformation_id),
            )
            for transformation_id in transformation_ids
            if self._session.get(
                EventModelTransformationModel,
                {
                    "event_id": str(event.id),
                    "transformation_id": str(transformation_id),
                },
            )
            is None
        )
        run_identity = {
            "event_id": str(event.id),
            "processing_run_id": str(processing_run_id),
        }
        if self._session.get(EventProcessingRunModel, run_identity) is None:
            self._session.add(EventProcessingRunModel(**run_identity))
        self._session.flush()
        return persisted

    def transformation_ids(self, event_id: UUID) -> tuple[UUID, ...]:
        return tuple(
            UUID(value)
            for value in self._session.scalars(
                select(EventModelTransformationModel.transformation_id)
                .where(EventModelTransformationModel.event_id == str(event_id))
                .order_by(EventModelTransformationModel.transformation_id)
            )
        )

    def processing_run_ids(self, event_id: UUID) -> tuple[UUID, ...]:
        return tuple(
            UUID(value)
            for value in self._session.scalars(
                select(EventProcessingRunModel.processing_run_id)
                .where(EventProcessingRunModel.event_id == str(event_id))
                .order_by(EventProcessingRunModel.processing_run_id)
            )
        )

    def events_for_processing_run(self, processing_run_id: UUID) -> tuple[Event, ...]:
        models = self._session.scalars(
            select(EventModel)
            .join(
                EventProcessingRunModel,
                EventProcessingRunModel.event_id == EventModel.id,
            )
            .where(EventProcessingRunModel.processing_run_id == str(processing_run_id))
            .order_by(EventModel.started_at, EventModel.id)
        )
        return tuple(_event_from_model(model) for model in models)


class EventCorrectionRepository:
    """Persist one append-only correction chain per stable event lineage."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def save(self, correction: EventCorrection) -> EventCorrection:
        existing = self._session.scalar(
            select(EventCorrectionModel).where(
                EventCorrectionModel.idempotency_key == correction.idempotency_key
            )
        )
        if existing is not None:
            persisted = _event_correction_from_model(existing)
            if persisted != correction:
                raise DatabaseError("Event correction idempotency identity conflicts")
            return persisted

        target = self._session.get(EventModel, str(correction.target_event_id))
        if target is None:
            raise DatabaseError("Cannot correct a missing event")
        if target.lineage_key != correction.event_lineage_key:
            raise DatabaseError("Event correction lineage does not match its target")

        latest = self.latest_for_lineages((correction.event_lineage_key,)).get(
            correction.event_lineage_key
        )
        expected_superseded = None if latest is None else latest.id
        if correction.supersedes_correction_id != expected_superseded:
            raise DatabaseError("Event correction must extend the latest lineage state")

        self._session.add(_event_correction_to_model(correction))
        self._session.flush()
        return correction

    def by_id(self, correction_id: UUID) -> EventCorrection | None:
        model = self._session.get(EventCorrectionModel, str(correction_id))
        return None if model is None else _event_correction_from_model(model)

    def list(self, *, limit: int = 100) -> tuple[EventCorrection, ...]:
        """Load recent append-only event corrections."""
        _validate_inspection_limit(limit)
        models = self._session.scalars(
            select(EventCorrectionModel)
            .order_by(
                EventCorrectionModel.created_at.desc(),
                EventCorrectionModel.id.desc(),
            )
            .limit(limit)
        )
        return tuple(_event_correction_from_model(model) for model in models)

    def latest_for_lineages(
        self,
        lineage_keys: tuple[str, ...],
    ) -> dict[str, EventCorrection]:
        if not lineage_keys:
            return {}
        models = tuple(
            self._session.scalars(
                select(EventCorrectionModel)
                .where(EventCorrectionModel.event_lineage_key.in_(lineage_keys))
                .order_by(
                    EventCorrectionModel.created_at,
                    EventCorrectionModel.id,
                )
            )
        )
        superseded_ids = {
            model.supersedes_correction_id
            for model in models
            if model.supersedes_correction_id is not None
        }
        latest: dict[str, EventCorrection] = {}
        for model in models:
            if model.id not in superseded_ids:
                if model.event_lineage_key in latest:
                    raise DatabaseError("Event correction lineage is forked")
                latest[model.event_lineage_key] = _event_correction_from_model(model)
        return latest


class TimelineBuildRepository:
    """Persist content-free replay parameters linked to one processing run."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def save(self, build: TimelineBuild) -> TimelineBuild:
        existing = self._session.get(
            TimelineBuildModel,
            str(build.processing_run_id),
        )
        if existing is not None:
            persisted = _timeline_build_from_model(existing)
            if persisted != build:
                raise DatabaseError("Timeline build identity conflicts")
            return persisted
        run = self._session.get(ProcessingRunModel, str(build.processing_run_id))
        if run is None:
            raise DatabaseError("Timeline build processing run is missing")
        if (
            run.pipeline != "activity_timeline"
            or run.version != build.processing_version
        ):
            raise DatabaseError("Timeline build processing identity is invalid")
        self._session.add(_timeline_build_to_model(build))
        self._session.flush()
        return build

    def by_processing_run(self, processing_run_id: UUID) -> TimelineBuild | None:
        model = self._session.get(TimelineBuildModel, str(processing_run_id))
        return None if model is None else _timeline_build_from_model(model)

    def matching_successful(
        self,
        *,
        processing_version: str,
        window_start: datetime,
        window_end: datetime,
        session_gap_seconds: int,
        max_session_duration_seconds: int,
        limit: int = 100,
    ) -> tuple[TimelineBuild, ...]:
        """Load recent successful builds with identical replay parameters."""
        _validate_inspection_limit(limit)
        models = self._session.scalars(
            select(TimelineBuildModel)
            .join(
                ProcessingRunModel,
                ProcessingRunModel.id == TimelineBuildModel.processing_run_id,
            )
            .where(
                TimelineBuildModel.processing_version == processing_version,
                TimelineBuildModel.window_start == format_utc(window_start),
                TimelineBuildModel.window_end == format_utc(window_end),
                TimelineBuildModel.session_gap_seconds == session_gap_seconds,
                TimelineBuildModel.max_session_duration_seconds
                == max_session_duration_seconds,
                ProcessingRunModel.status == ProcessingRunStatus.SUCCEEDED.value,
            )
            .order_by(
                ProcessingRunModel.started_at.desc(),
                ProcessingRunModel.id.desc(),
            )
            .limit(limit)
        )
        return tuple(_timeline_build_from_model(model) for model in models)


class PatternRepository:
    """Persist replayable patterns with event and processing-run provenance."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def save(self, pattern: Pattern, *, processing_run_id: UUID) -> Pattern:
        existing = self._session.scalar(
            select(PatternModel).where(
                PatternModel.idempotency_key == pattern.idempotency_key
            )
        )
        if existing is None:
            PipelineRepository(self._session)._require_ids(
                EventModel,
                pattern.source_event_ids,
                "event",
            )
            self._session.add(_pattern_to_model(pattern))
            self._session.flush()
            self._session.add_all(
                PatternEventModel(pattern_id=str(pattern.id), event_id=str(event_id))
                for event_id in pattern.source_event_ids
            )
            persisted = pattern
        else:
            persisted = _pattern_from_model(existing)
            if persisted.id != pattern.id:
                raise DatabaseError("Pattern idempotency identity conflicts")

        run = self._session.get(ProcessingRunModel, str(processing_run_id))
        if (
            run is None
            or run.pipeline != "patterns"
            or run.version != pattern.processing_version
        ):
            raise DatabaseError("Pattern processing run is missing or invalid")
        identity = {
            "pattern_id": str(persisted.id),
            "processing_run_id": str(processing_run_id),
        }
        if self._session.get(PatternProcessingRunModel, identity) is None:
            self._session.add(PatternProcessingRunModel(**identity))
        self._session.flush()
        return persisted

    def by_id(self, pattern_id: UUID) -> Pattern | None:
        model = self._session.get(PatternModel, str(pattern_id))
        return None if model is None else _pattern_from_model(model)

    def list(self, *, limit: int = 100) -> tuple[Pattern, ...]:
        """Load recent detected patterns for local inspection."""
        _validate_inspection_limit(limit)
        models = self._session.scalars(
            select(PatternModel)
            .order_by(PatternModel.created_at.desc(), PatternModel.id.desc())
            .limit(limit)
        )
        return tuple(_pattern_from_model(model) for model in models)

    def patterns_for_processing_run(
        self,
        processing_run_id: UUID,
    ) -> tuple[Pattern, ...]:
        models = self._session.scalars(
            select(PatternModel)
            .join(
                PatternProcessingRunModel,
                PatternProcessingRunModel.pattern_id == PatternModel.id,
            )
            .where(
                PatternProcessingRunModel.processing_run_id == str(processing_run_id)
            )
            .order_by(PatternModel.window_start, PatternModel.type, PatternModel.id)
        )
        return tuple(_pattern_from_model(model) for model in models)

    def event_ids(self, pattern_id: UUID) -> tuple[UUID, ...]:
        return tuple(
            UUID(value)
            for value in self._session.scalars(
                select(PatternEventModel.event_id)
                .where(PatternEventModel.pattern_id == str(pattern_id))
                .order_by(PatternEventModel.event_id)
            )
        )


class PatternBuildRepository:
    """Persist content-free pattern replay inputs."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def save(self, build: PatternBuild) -> PatternBuild:
        existing = self._session.get(PatternBuildModel, str(build.processing_run_id))
        if existing is not None:
            persisted = _pattern_build_from_model(existing)
            if persisted != build:
                raise DatabaseError("Pattern build identity conflicts")
            return persisted
        run = self._session.get(ProcessingRunModel, str(build.processing_run_id))
        source = self._session.get(
            ProcessingRunModel,
            str(build.source_timeline_run_id),
        )
        if (
            run is None
            or run.pipeline != "patterns"
            or run.version != build.processing_version
            or source is None
            or source.pipeline != "activity_timeline"
            or source.status != ProcessingRunStatus.SUCCEEDED.value
        ):
            raise DatabaseError("Pattern build processing identity is invalid")
        self._session.add(_pattern_build_to_model(build))
        self._session.flush()
        return build

    def by_processing_run(self, processing_run_id: UUID) -> PatternBuild | None:
        model = self._session.get(PatternBuildModel, str(processing_run_id))
        return None if model is None else _pattern_build_from_model(model)

    def successful_for_source(
        self,
        source_timeline_run_id: UUID,
        *,
        limit: int = 100,
    ) -> tuple[PatternBuild, ...]:
        """Load recent successful pattern builds for one timeline snapshot."""
        _validate_inspection_limit(limit)
        models = self._session.scalars(
            select(PatternBuildModel)
            .join(
                ProcessingRunModel,
                ProcessingRunModel.id == PatternBuildModel.processing_run_id,
            )
            .where(
                PatternBuildModel.source_timeline_run_id == str(source_timeline_run_id),
                ProcessingRunModel.status == ProcessingRunStatus.SUCCEEDED.value,
            )
            .order_by(
                ProcessingRunModel.started_at.desc(),
                ProcessingRunModel.id.desc(),
            )
            .limit(limit)
        )
        return tuple(_pattern_build_from_model(model) for model in models)


class CandidateBuildRepository:
    """Persist and read pattern-to-candidate replay provenance."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def save(self, build: CandidateBuild) -> CandidateBuild:
        existing = self._session.get(CandidateBuildModel, str(build.processing_run_id))
        if existing is not None:
            persisted = _candidate_build_from_model(existing)
            if persisted != build:
                raise DatabaseError("Candidate build identity conflicts")
            return persisted
        run = self._session.get(ProcessingRunModel, str(build.processing_run_id))
        source = self._session.get(
            ProcessingRunModel,
            str(build.source_pattern_run_id),
        )
        if (
            run is None
            or run.pipeline != "pattern_candidates"
            or run.version != build.processing_version
            or source is None
            or source.pipeline != "patterns"
            or source.status != ProcessingRunStatus.SUCCEEDED.value
        ):
            raise DatabaseError("Candidate build processing identity is invalid")
        self._session.add(_candidate_build_to_model(build))
        self._session.flush()
        return build

    def by_processing_run(self, processing_run_id: UUID) -> CandidateBuild | None:
        model = self._session.get(CandidateBuildModel, str(processing_run_id))
        return None if model is None else _candidate_build_from_model(model)

    def successful_for_source(
        self,
        source_pattern_run_id: UUID,
        *,
        limit: int = 100,
    ) -> tuple[CandidateBuild, ...]:
        """Load recent successful candidate builds for one pattern snapshot."""
        _validate_inspection_limit(limit)
        models = self._session.scalars(
            select(CandidateBuildModel)
            .join(
                ProcessingRunModel,
                ProcessingRunModel.id == CandidateBuildModel.processing_run_id,
            )
            .where(
                CandidateBuildModel.source_pattern_run_id == str(source_pattern_run_id),
                ProcessingRunModel.status == ProcessingRunStatus.SUCCEEDED.value,
            )
            .order_by(
                ProcessingRunModel.started_at.desc(),
                ProcessingRunModel.id.desc(),
            )
            .limit(limit)
        )
        return tuple(_candidate_build_from_model(model) for model in models)


class PatternCandidateRepository:
    """Persist pattern candidates and their producing run without changing state."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def save(
        self,
        candidate: MemoryCandidate,
        *,
        processing_run_id: UUID,
    ) -> MemoryCandidate:
        if candidate.source_type != "pattern":
            raise DatabaseError("Pattern candidate source type is invalid")
        run = self._session.get(ProcessingRunModel, str(processing_run_id))
        build = self._session.get(
            CandidateBuildModel,
            str(processing_run_id),
        )
        if (
            run is None
            or run.pipeline != "pattern_candidates"
            or run.version != candidate.scoring_version
            or build is None
        ):
            raise DatabaseError("Candidate processing run is missing or invalid")
        source_pattern_ids = set(
            self._session.scalars(
                select(PatternProcessingRunModel.pattern_id).where(
                    PatternProcessingRunModel.processing_run_id
                    == build.source_pattern_run_id
                )
            )
        )
        if not {str(item) for item in candidate.source_ids} <= source_pattern_ids:
            raise DatabaseError(
                "Candidate pattern provenance is outside its source run"
            )
        persisted = PipelineRepository(self._session).save_candidate(candidate)
        identity = {
            "candidate_id": str(persisted.id),
            "processing_run_id": str(processing_run_id),
        }
        if self._session.get(CandidateProcessingRunModel, identity) is None:
            self._session.add(CandidateProcessingRunModel(**identity))
        self._session.flush()
        return persisted

    def candidates_for_processing_run(
        self,
        processing_run_id: UUID,
    ) -> tuple[MemoryCandidate, ...]:
        models = self._session.scalars(
            select(MemoryCandidateModel)
            .join(
                CandidateProcessingRunModel,
                CandidateProcessingRunModel.candidate_id == MemoryCandidateModel.id,
            )
            .where(
                CandidateProcessingRunModel.processing_run_id == str(processing_run_id)
            )
            .order_by(MemoryCandidateModel.id)
        )
        return tuple(_candidate_from_model(model) for model in models)

    def pattern_ids(self, candidate_id: UUID) -> tuple[UUID, ...]:
        return tuple(
            UUID(value)
            for value in self._session.scalars(
                select(CandidatePatternModel.pattern_id)
                .where(CandidatePatternModel.candidate_id == str(candidate_id))
                .order_by(CandidatePatternModel.pattern_id)
            )
        )


class CandidateDecisionRepository:
    """Persist append-only candidate evaluations for replay comparison."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def save(self, decision: CandidateDecision) -> CandidateDecision:
        existing = self._session.scalar(
            select(CandidateDecisionModel).where(
                CandidateDecisionModel.idempotency_key == decision.idempotency_key
            )
        )
        if existing is not None:
            persisted = _candidate_decision_from_model(existing)
            if persisted != decision:
                raise DatabaseError("Candidate decision identity conflicts")
            return persisted
        if self._session.get(MemoryCandidateModel, str(decision.candidate_id)) is None:
            raise DatabaseError("Candidate decision candidate is missing")
        run = self._session.get(ProcessingRunModel, str(decision.processing_run_id))
        build = self._session.get(
            CandidateEvaluationBuildModel,
            str(decision.processing_run_id),
        )
        if (
            run is None
            or run.pipeline != "candidate_evaluation"
            or run.version != decision.policy_version
            or build is None
            or build.policy_version != decision.policy_version
            or build.acceptance_threshold != decision.acceptance_threshold
        ):
            raise DatabaseError("Candidate decision processing run is invalid")
        candidate_link = self._session.get(
            CandidateProcessingRunModel,
            {
                "candidate_id": str(decision.candidate_id),
                "processing_run_id": build.source_candidate_run_id,
            },
        )
        if candidate_link is None:
            raise DatabaseError(
                "Candidate decision provenance is outside its source run"
            )
        self._session.add(_candidate_decision_to_model(decision))
        self._session.flush()
        return decision

    def decisions_for_processing_run(
        self,
        processing_run_id: UUID,
    ) -> tuple[CandidateDecision, ...]:
        models = self._session.scalars(
            select(CandidateDecisionModel)
            .where(CandidateDecisionModel.processing_run_id == str(processing_run_id))
            .order_by(CandidateDecisionModel.candidate_id)
        )
        return tuple(_candidate_decision_from_model(model) for model in models)

    def list(self, *, limit: int = 100) -> tuple[CandidateDecision, ...]:
        """Load recent append-only promotion decisions."""
        _validate_inspection_limit(limit)
        models = self._session.scalars(
            select(CandidateDecisionModel)
            .order_by(
                CandidateDecisionModel.created_at.desc(),
                CandidateDecisionModel.id.desc(),
            )
            .limit(limit)
        )
        return tuple(_candidate_decision_from_model(model) for model in models)


class CandidateEvaluationBuildRepository:
    """Persist the full content-free policy used by an evaluation run."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def save(self, build: CandidateEvaluationBuild) -> CandidateEvaluationBuild:
        existing = self._session.get(
            CandidateEvaluationBuildModel,
            str(build.processing_run_id),
        )
        if existing is not None:
            persisted = _candidate_evaluation_build_from_model(existing)
            if persisted != build:
                raise DatabaseError("Candidate evaluation build identity conflicts")
            return persisted
        run = self._session.get(ProcessingRunModel, str(build.processing_run_id))
        source = self._session.get(
            ProcessingRunModel,
            str(build.source_candidate_run_id),
        )
        if (
            run is None
            or run.pipeline != "candidate_evaluation"
            or run.version != build.policy_version
            or source is None
            or source.pipeline != "pattern_candidates"
            or source.status != ProcessingRunStatus.SUCCEEDED.value
        ):
            raise DatabaseError("Candidate evaluation build identity is invalid")
        self._session.add(_candidate_evaluation_build_to_model(build))
        self._session.flush()
        return build

    def by_processing_run(
        self,
        processing_run_id: UUID,
    ) -> CandidateEvaluationBuild | None:
        model = self._session.get(
            CandidateEvaluationBuildModel,
            str(processing_run_id),
        )
        return None if model is None else _candidate_evaluation_build_from_model(model)

    def successful_for_source(
        self,
        source_candidate_run_id: UUID,
        *,
        limit: int = 100,
    ) -> tuple[CandidateEvaluationBuild, ...]:
        """Load recent successful evaluations for one candidate snapshot."""
        _validate_inspection_limit(limit)
        models = self._session.scalars(
            select(CandidateEvaluationBuildModel)
            .join(
                ProcessingRunModel,
                ProcessingRunModel.id
                == CandidateEvaluationBuildModel.processing_run_id,
            )
            .where(
                CandidateEvaluationBuildModel.source_candidate_run_id
                == str(source_candidate_run_id),
                ProcessingRunModel.status == ProcessingRunStatus.SUCCEEDED.value,
            )
            .order_by(
                ProcessingRunModel.started_at.desc(),
                ProcessingRunModel.id.desc(),
            )
            .limit(limit)
        )
        return tuple(_candidate_evaluation_build_from_model(model) for model in models)


class MemoryPromotionBuildRepository:
    """Persist the selected candidate-evaluation snapshot for promotion."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def save(self, build: MemoryPromotionBuild) -> MemoryPromotionBuild:
        existing = self._session.get(
            MemoryPromotionBuildModel,
            str(build.processing_run_id),
        )
        if existing is not None:
            persisted = _memory_promotion_build_from_model(existing)
            if persisted != build:
                raise DatabaseError("Memory promotion build identity conflicts")
            return persisted
        run = self._session.get(ProcessingRunModel, str(build.processing_run_id))
        source = self._session.get(
            ProcessingRunModel,
            str(build.source_evaluation_run_id),
        )
        if (
            run is None
            or run.pipeline != "memory_promotion"
            or run.version != build.processing_version
            or source is None
            or source.pipeline != "candidate_evaluation"
            or source.status != ProcessingRunStatus.SUCCEEDED.value
        ):
            raise DatabaseError("Memory promotion build identity is invalid")
        self._session.add(_memory_promotion_build_to_model(build))
        self._session.flush()
        return build

    def by_processing_run(
        self,
        processing_run_id: UUID,
    ) -> MemoryPromotionBuild | None:
        model = self._session.get(
            MemoryPromotionBuildModel,
            str(processing_run_id),
        )
        return None if model is None else _memory_promotion_build_from_model(model)

    def successful_for_source(
        self,
        source_evaluation_run_id: UUID,
        *,
        limit: int = 100,
    ) -> tuple[MemoryPromotionBuild, ...]:
        """Load recent successful promotions for one evaluation snapshot."""
        _validate_inspection_limit(limit)
        models = self._session.scalars(
            select(MemoryPromotionBuildModel)
            .join(
                ProcessingRunModel,
                ProcessingRunModel.id == MemoryPromotionBuildModel.processing_run_id,
            )
            .where(
                MemoryPromotionBuildModel.source_evaluation_run_id
                == str(source_evaluation_run_id),
                ProcessingRunModel.status == ProcessingRunStatus.SUCCEEDED.value,
            )
            .order_by(
                ProcessingRunModel.started_at.desc(),
                ProcessingRunModel.id.desc(),
            )
            .limit(limit)
        )
        return tuple(_memory_promotion_build_from_model(model) for model in models)


class MemoryPromotionRepository:
    """Persist durable links selected by replayable promotion runs."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def save(self, link: MemoryLink, *, processing_run_id: UUID) -> MemoryLink:
        run = self._session.get(ProcessingRunModel, str(processing_run_id))
        if run is None or run.pipeline != "memory_promotion":
            raise DatabaseError("Memory promotion run is missing or invalid")
        persisted = PipelineRepository(self._session).save_memory_link(link)
        identity = {
            "memory_link_id": str(persisted.id),
            "processing_run_id": str(processing_run_id),
        }
        if self._session.get(MemoryLinkProcessingRunModel, identity) is None:
            self._session.add(MemoryLinkProcessingRunModel(**identity))
        self._session.flush()
        return persisted

    def links_for_processing_run(
        self,
        processing_run_id: UUID,
    ) -> tuple[MemoryLink, ...]:
        models = self._session.scalars(
            select(MemoryLinkModel)
            .join(
                MemoryLinkProcessingRunModel,
                MemoryLinkProcessingRunModel.memory_link_id == MemoryLinkModel.id,
            )
            .where(
                MemoryLinkProcessingRunModel.processing_run_id == str(processing_run_id)
            )
            .order_by(MemoryLinkModel.id)
        )
        return tuple(_memory_link_from_model(model) for model in models)


class MemoryCorrectionRepository:
    """Stage and atomically finalize one append-only memory supersession."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def stage(
        self,
        *,
        candidate: MemoryCandidate,
        link: MemoryLink,
        build: MemoryCorrectionBuild,
    ) -> tuple[MemoryCandidate, MemoryLink, MemoryCorrectionBuild]:
        if (
            candidate.status is not CandidateStatus.ACCEPTED
            or candidate.source_type != "memory_correction"
            or link.status is not MemoryLinkStatus.PENDING
            or link.supersedes_memory_id is None
            or build.candidate_id != candidate.id
            or build.target_memory_id != link.supersedes_memory_id
        ):
            raise DatabaseError("Memory correction staging state is invalid")
        pipeline = PipelineRepository(self._session)
        existing = pipeline.memory_link_by_candidate_id(candidate.id)
        if existing is not None:
            persisted_candidate = pipeline.candidate_by_id(candidate.id)
            persisted_build = self.build_by_candidate(candidate.id)
            if (
                persisted_candidate is None
                or persisted_build is None
                or existing.id != link.id
                or persisted_build != build
            ):
                raise DatabaseError("Memory correction identity conflicts")
            return persisted_candidate, existing, persisted_build
        successor = pipeline.memory_link_superseding(link.supersedes_memory_id)
        if successor is not None:
            raise DatabaseError("Memory already has a pending or applied correction")
        persisted_candidate = pipeline.save_candidate(candidate)
        persisted_link = pipeline.save_memory_link(link)
        self._session.add(_memory_correction_build_to_model(build))
        self._session.flush()
        return persisted_candidate, persisted_link, build

    def build_by_candidate(
        self,
        candidate_id: UUID,
    ) -> MemoryCorrectionBuild | None:
        model = self._session.get(MemoryCorrectionBuildModel, str(candidate_id))
        return None if model is None else _memory_correction_build_from_model(model)

    def list_builds(self, *, limit: int = 100) -> tuple[MemoryCorrectionBuild, ...]:
        """Load recent content-free memory-correction provenance."""
        _validate_inspection_limit(limit)
        models = self._session.scalars(
            select(MemoryCorrectionBuildModel)
            .order_by(
                MemoryCorrectionBuildModel.ended_at.desc(),
                MemoryCorrectionBuildModel.candidate_id.desc(),
            )
            .limit(limit)
        )
        return tuple(_memory_correction_build_from_model(model) for model in models)

    def finalize(
        self,
        *,
        candidate_id: UUID,
        memory_link_id: UUID,
        backend_id: str,
        processed_at: datetime,
    ) -> tuple[MemoryCandidate, MemoryLink]:
        normalized_backend_id = backend_id.strip()
        if (
            not normalized_backend_id
            or len(normalized_backend_id) > 255
            or any(character in normalized_backend_id for character in "\r\n")
        ):
            raise DatabaseError("Memory correction backend identity is invalid")
        candidate_model = self._session.get(
            MemoryCandidateModel,
            str(candidate_id),
        )
        link_model = self._session.get(MemoryLinkModel, str(memory_link_id))
        if (
            candidate_model is None
            or link_model is None
            or link_model.candidate_id != str(candidate_id)
            or link_model.supersedes_memory_id is None
        ):
            raise DatabaseError("Staged memory correction is unavailable")
        if link_model.status == MemoryLinkStatus.ACTIVE.value:
            return (
                _candidate_from_model(candidate_model),
                _memory_link_from_model(link_model),
            )
        target_model = self._session.get(
            MemoryLinkModel,
            link_model.supersedes_memory_id,
        )
        if (
            link_model.status != MemoryLinkStatus.PENDING.value
            or candidate_model.status != CandidateStatus.ACCEPTED.value
            or target_model is None
            or target_model.status != MemoryLinkStatus.ACTIVE.value
        ):
            raise DatabaseError("Memory correction cannot be finalized")
        backend_owner = self._session.scalar(
            select(MemoryLinkModel).where(
                MemoryLinkModel.memory_backend_id == normalized_backend_id,
                MemoryLinkModel.id != link_model.id,
            )
        )
        if backend_owner is not None:
            raise DatabaseError("Memory correction backend identity conflicts")

        stored = _candidate_from_model(candidate_model).mark_stored(
            processed_at=processed_at
        )
        _update_candidate_state(candidate_model, stored)
        link_model.memory_backend_id = normalized_backend_id
        link_model.status = MemoryLinkStatus.ACTIVE.value
        target_model.status = MemoryLinkStatus.SUPERSEDED.value
        self._session.flush()
        return stored, _memory_link_from_model(link_model)


class AgentProposalRepository:
    """Persist agent submissions outside the final-memory write path."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def save(self, proposal: AgentProposal) -> AgentProposal:
        existing = self._session.scalar(
            select(AgentProposalModel).where(
                AgentProposalModel.idempotency_key == proposal.idempotency_key
            )
        )
        if existing is not None:
            persisted = _agent_proposal_from_model(existing)
            identity = (
                persisted.agent_id,
                persisted.agent_role,
                persisted.text,
                persisted.proposal_type,
                persisted.reference_type,
                persisted.reference_id,
            )
            proposed_identity = (
                proposal.agent_id,
                proposal.agent_role,
                proposal.text,
                proposal.proposal_type,
                proposal.reference_type,
                proposal.reference_id,
            )
            if identity != proposed_identity:
                raise DatabaseError("Agent proposal identity conflicts")
            return persisted
        self._session.add(_agent_proposal_to_model(proposal))
        self._session.flush()
        return proposal

    def by_id(self, proposal_id: UUID) -> AgentProposal | None:
        model = self._session.get(AgentProposalModel, str(proposal_id))
        return None if model is None else _agent_proposal_from_model(model)

    def list(
        self,
        *,
        status: AgentProposalStatus | None = None,
        limit: int = 100,
    ) -> tuple[AgentProposal, ...]:
        _validate_inspection_limit(limit)
        statement = select(AgentProposalModel)
        if status is not None:
            statement = statement.where(AgentProposalModel.status == status.value)
        models = self._session.scalars(
            statement.order_by(
                AgentProposalModel.created_at.desc(), AgentProposalModel.id.desc()
            ).limit(limit)
        )
        return tuple(_agent_proposal_from_model(model) for model in models)


class AgentProposalAdoptionRepository:
    """Persist and resume explicit, locally validated proposal adoption."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def build_by_proposal(
        self,
        proposal_id: UUID,
    ) -> AgentProposalAdoptionBuild | None:
        model = self._session.get(
            AgentProposalAdoptionBuildModel,
            str(proposal_id),
        )
        return (
            None if model is None else _agent_proposal_adoption_build_from_model(model)
        )

    def build_by_candidate(
        self,
        candidate_id: UUID,
    ) -> AgentProposalAdoptionBuild | None:
        model = self._session.scalar(
            select(AgentProposalAdoptionBuildModel).where(
                AgentProposalAdoptionBuildModel.candidate_id == str(candidate_id)
            )
        )
        return (
            None if model is None else _agent_proposal_adoption_build_from_model(model)
        )

    def list_builds(
        self, *, limit: int = 100
    ) -> tuple[AgentProposalAdoptionBuild, ...]:
        """Load recent content-free proposal validation provenance."""
        _validate_inspection_limit(limit)
        models = self._session.scalars(
            select(AgentProposalAdoptionBuildModel)
            .order_by(
                AgentProposalAdoptionBuildModel.ended_at.desc(),
                AgentProposalAdoptionBuildModel.proposal_id.desc(),
            )
            .limit(limit)
        )
        return tuple(
            _agent_proposal_adoption_build_from_model(model) for model in models
        )

    def stage_acceptance(
        self,
        *,
        candidate: MemoryCandidate,
        link: MemoryLink,
        build: AgentProposalAdoptionBuild,
    ) -> tuple[MemoryCandidate, MemoryLink, AgentProposalAdoptionBuild]:
        if (
            candidate.status is not CandidateStatus.ACCEPTED
            or candidate.source_type != "agent_proposal"
            or len(candidate.source_ids) != 1
            or link.status is not MemoryLinkStatus.PENDING
            or link.supersedes_memory_id is not None
            or build.decision is not AgentProposalDecision.ACCEPTED
            or build.proposal_id != candidate.source_ids[0]
            or build.candidate_id != candidate.id
        ):
            raise DatabaseError("Agent proposal adoption staging state is invalid")
        pipeline = PipelineRepository(self._session)
        existing_build = self.build_by_proposal(build.proposal_id)
        existing_candidate = pipeline.candidate_by_id(candidate.id)
        existing_link = pipeline.memory_link_by_candidate_id(candidate.id)
        if existing_build is not None:
            if (
                existing_build != build
                or existing_candidate is None
                or existing_link is None
                or existing_link.id != link.id
            ):
                raise DatabaseError("Agent proposal adoption identity conflicts")
            return existing_candidate, existing_link, existing_build
        if existing_candidate is not None or existing_link is not None:
            raise DatabaseError("Agent proposal adoption staging is incomplete")
        proposal = AgentProposalRepository(self._session).by_id(build.proposal_id)
        if proposal is None or proposal.status is not AgentProposalStatus.PENDING:
            raise DatabaseError("Agent proposal is unavailable for adoption")
        persisted_candidate = pipeline.save_candidate(candidate)
        self._session.add(_agent_proposal_adoption_build_to_model(build))
        self._session.flush()
        persisted_link = pipeline.save_memory_link(link)
        return persisted_candidate, persisted_link, build

    def record_decision(
        self,
        *,
        build: AgentProposalAdoptionBuild,
        processed_at: datetime,
    ) -> AgentProposal:
        if (
            build.decision is AgentProposalDecision.ACCEPTED
            or build.candidate_id is not None
        ):
            raise DatabaseError("Agent proposal terminal decision is invalid")
        existing_build = self.build_by_proposal(build.proposal_id)
        proposal_model = self._session.get(AgentProposalModel, str(build.proposal_id))
        if proposal_model is None:
            raise DatabaseError("Agent proposal was not found")
        proposal = _agent_proposal_from_model(proposal_model)
        status = AgentProposalStatus(build.decision.value)
        if existing_build is not None:
            if (
                existing_build != build
                or proposal.status is not status
                or proposal.reason != build.reason_code.value
            ):
                raise DatabaseError("Agent proposal decision identity conflicts")
            return proposal
        if proposal.status is not AgentProposalStatus.PENDING:
            raise DatabaseError("Only pending agent proposals can be evaluated")
        decided = proposal.decide(
            status,
            processed_at=processed_at,
            reason=build.reason_code.value,
        )
        self._session.add(_agent_proposal_adoption_build_to_model(build))
        proposal_model.status = decided.status.value
        proposal_model.reason = decided.reason
        proposal_model.processed_at = format_utc(processed_at)
        self._session.flush()
        return decided

    def finalize(
        self,
        *,
        proposal_id: UUID,
        candidate_id: UUID,
        memory_link_id: UUID,
        backend_id: str,
        processed_at: datetime,
    ) -> tuple[AgentProposal, MemoryCandidate, MemoryLink]:
        normalized_backend_id = backend_id.strip()
        if (
            not normalized_backend_id
            or len(normalized_backend_id) > 255
            or any(character in normalized_backend_id for character in "\r\n")
        ):
            raise DatabaseError("Agent proposal backend identity is invalid")
        proposal_model = self._session.get(AgentProposalModel, str(proposal_id))
        candidate_model = self._session.get(MemoryCandidateModel, str(candidate_id))
        link_model = self._session.get(MemoryLinkModel, str(memory_link_id))
        build_model = self._session.get(
            AgentProposalAdoptionBuildModel,
            str(proposal_id),
        )
        if (
            proposal_model is None
            or candidate_model is None
            or link_model is None
            or link_model.candidate_id != str(candidate_id)
            or build_model is None
            or build_model.candidate_id != str(candidate_id)
            or build_model.decision != AgentProposalDecision.ACCEPTED.value
        ):
            raise DatabaseError("Staged agent proposal adoption is unavailable")
        if proposal_model.status == AgentProposalStatus.ADOPTED.value:
            if (
                candidate_model.status != CandidateStatus.STORED.value
                or link_model.status != MemoryLinkStatus.ACTIVE.value
            ):
                raise DatabaseError("Finalized agent proposal adoption is inconsistent")
            return (
                _agent_proposal_from_model(proposal_model),
                _candidate_from_model(candidate_model),
                _memory_link_from_model(link_model),
            )
        if (
            proposal_model.status != AgentProposalStatus.PENDING.value
            or candidate_model.status != CandidateStatus.ACCEPTED.value
            or link_model.status != MemoryLinkStatus.PENDING.value
        ):
            raise DatabaseError("Agent proposal adoption cannot be finalized")
        backend_owner = self._session.scalar(
            select(MemoryLinkModel).where(
                MemoryLinkModel.memory_backend_id == normalized_backend_id,
                MemoryLinkModel.id != link_model.id,
            )
        )
        if backend_owner is not None:
            raise DatabaseError("Agent proposal backend identity conflicts")
        stored = _candidate_from_model(candidate_model).mark_stored(
            processed_at=processed_at
        )
        _update_candidate_state(candidate_model, stored)
        link_model.memory_backend_id = normalized_backend_id
        link_model.status = MemoryLinkStatus.ACTIVE.value
        proposal_model.status = AgentProposalStatus.ADOPTED.value
        proposal_model.reason = None
        proposal_model.processed_at = format_utc(processed_at)
        self._session.flush()
        return (
            _agent_proposal_from_model(proposal_model),
            stored,
            _memory_link_from_model(link_model),
        )

    def reject(
        self,
        *,
        proposal_id: UUID,
        processed_at: datetime,
        reason: str,
    ) -> AgentProposal:
        normalized_reason = reason.strip()
        if (
            not normalized_reason
            or len(normalized_reason) > 255
            or any(character in normalized_reason for character in "\r\n")
        ):
            raise DatabaseError("Agent proposal rejection reason is invalid")
        proposal_model = self._session.get(AgentProposalModel, str(proposal_id))
        if proposal_model is None:
            raise DatabaseError("Agent proposal was not found")
        proposal = _agent_proposal_from_model(proposal_model)
        if proposal.status is AgentProposalStatus.REJECTED:
            return proposal
        if proposal.status is not AgentProposalStatus.PENDING:
            raise DatabaseError("Only pending agent proposals can be rejected")
        if self.build_by_proposal(proposal_id) is not None:
            raise DatabaseError("Agent proposal adoption is already staged")
        decided = proposal.decide(
            AgentProposalStatus.REJECTED,
            processed_at=processed_at,
            reason=normalized_reason,
        )
        proposal_model.status = decided.status.value
        proposal_model.reason = decided.reason
        proposal_model.processed_at = format_utc(processed_at)
        self._session.flush()
        return decided


class CollectionRepository:
    """Persist collection control and pre-capture exclusion policy."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_control(self, *, default_at: datetime) -> CollectionControl:
        timestamp = parse_utc(format_utc(default_at))
        model = self._session.get(CollectionControlModel, 1)
        if model is None:
            model = CollectionControlModel(
                id=1,
                paused_at=None,
                pause_until=None,
                updated_at=format_utc(timestamp),
            )
            self._session.add(model)
            self._session.flush()
        return _collection_control_from_model(model)

    def save_control(self, control: CollectionControl) -> CollectionControl:
        model = self._session.get(CollectionControlModel, 1)
        if model is None:
            model = CollectionControlModel(id=1)
            self._session.add(model)
        model.paused_at = (
            None if control.paused_at is None else format_utc(control.paused_at)
        )
        model.pause_until = (
            None if control.pause_until is None else format_utc(control.pause_until)
        )
        model.updated_at = format_utc(control.updated_at)
        self._session.flush()
        return control

    def save_exclusion_rule(self, rule: ExclusionRule) -> ExclusionRule:
        existing = self._session.scalar(
            select(ExclusionRuleModel).where(
                ExclusionRuleModel.rule_type == rule.rule_type.value,
                ExclusionRuleModel.pattern == rule.pattern,
                ExclusionRuleModel.scope == rule.scope.value,
            )
        )
        if existing is not None:
            return _exclusion_rule_from_model(existing)
        self._session.add(_exclusion_rule_to_model(rule))
        self._session.flush()
        return rule

    def list_exclusion_rules(
        self, *, enabled_only: bool = False
    ) -> tuple[ExclusionRule, ...]:
        statement = select(ExclusionRuleModel).order_by(
            ExclusionRuleModel.built_in.desc(),
            ExclusionRuleModel.rule_type,
            ExclusionRuleModel.pattern,
        )
        if enabled_only:
            statement = statement.where(ExclusionRuleModel.enabled.is_(True))
        return tuple(
            _exclusion_rule_from_model(model)
            for model in self._session.scalars(statement)
        )

    def set_exclusion_rule_enabled(
        self, rule_id: UUID, *, enabled: bool, updated_at: datetime
    ) -> ExclusionRule:
        timestamp = parse_utc(format_utc(updated_at))
        model = self._session.get(ExclusionRuleModel, str(rule_id))
        if model is None:
            raise DatabaseError("The exclusion rule does not exist")
        model.enabled = enabled
        model.updated_at = format_utc(timestamp)
        self._session.flush()
        return _exclusion_rule_from_model(model)

    def delete_exclusion_rule(self, rule_id: UUID) -> None:
        model = self._session.get(ExclusionRuleModel, str(rule_id))
        if model is None:
            raise DatabaseError("The exclusion rule does not exist")
        if model.built_in:
            raise DatabaseError("Built-in exclusion rules can be disabled, not deleted")
        self._session.delete(model)
        self._session.flush()


class RawObservationRepository:
    """Find and tombstone expired raw observations without erasing provenance."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def expired(self, *, at: datetime, limit: int = 1000) -> tuple[Observation, ...]:
        if limit < 1:
            raise ValueError("raw purge limit must be positive")
        models = self._session.scalars(
            select(ObservationModel)
            .where(
                ObservationModel.expires_at <= format_utc(at),
                ObservationModel.processing_status != ObservationStatus.PURGED.value,
            )
            .order_by(ObservationModel.expires_at, ObservationModel.id)
            .limit(limit)
        )
        return tuple(_observation_from_model(model) for model in models)

    def expired_count(self, *, at: datetime) -> int:
        """Count raw records that should already have been tombstoned."""
        return int(
            self._session.scalar(
                select(func.count())
                .select_from(ObservationModel)
                .where(
                    ObservationModel.expires_at <= format_utc(at),
                    ObservationModel.processing_status
                    != ObservationStatus.PURGED.value,
                )
            )
            or 0
        )

    def excluded_capture_count(self, *, since: datetime) -> int:
        """Count persisted screenshot artifacts marked as excluded."""
        return int(
            self._session.scalar(
                select(func.count())
                .select_from(ObservationModel)
                .where(
                    ObservationModel.captured_at >= format_utc(since),
                    ObservationModel.source_type == SourceType.SCREENSHOT.value,
                    ObservationModel.excluded.is_(True),
                    ObservationModel.artifact_path.is_not(None),
                )
            )
            or 0
        )

    def retained(self, *, limit: int = 1000) -> tuple[Observation, ...]:
        """Load raw-bearing observations for an explicit immediate purge."""
        if limit < 1:
            raise ValueError("raw purge limit must be positive")
        models = self._session.scalars(
            select(ObservationModel)
            .where(
                ObservationModel.processing_status != ObservationStatus.PURGED.value,
            )
            .order_by(ObservationModel.expires_at, ObservationModel.id)
            .limit(limit)
        )
        return tuple(_observation_from_model(model) for model in models)

    def artifact_paths(self) -> frozenset[str]:
        return frozenset(
            path
            for path in self._session.scalars(
                select(ObservationModel.artifact_path).where(
                    ObservationModel.artifact_path.is_not(None),
                    ObservationModel.processing_status
                    != ObservationStatus.PURGED.value,
                )
            )
            if path is not None
        )

    def tombstone(self, observation_id: UUID) -> Observation:
        model = self._session.get(ObservationModel, str(observation_id))
        if model is None:
            raise DatabaseError("The raw observation does not exist")
        if model.processing_status == ObservationStatus.PURGED.value:
            return _observation_from_model(model)
        model.app_name = None
        model.app_bundle_id = None
        model.window_title = None
        model.artifact_path = None
        model.content_hash = None
        model.perceptual_hash = None
        model.processing_status = ObservationStatus.PURGED.value
        self._session.flush()
        return _observation_from_model(model)


def _validate_inspection_limit(limit: int) -> None:
    if not 1 <= limit <= 500:
        raise ValueError("inspection limit must be between 1 and 500")


def _observation_to_model(record: Observation) -> ObservationModel:
    return ObservationModel(
        id=str(record.id),
        idempotency_key=record.idempotency_key,
        source_type=record.source_type.value,
        activity_state=record.activity_state.value,
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
        expires_at=format_utc(record.expires_at),
        created_at=format_utc(record.created_at),
    )


def _collection_control_from_model(model: CollectionControlModel) -> CollectionControl:
    return CollectionControl(
        paused_at=None if model.paused_at is None else parse_utc(model.paused_at),
        pause_until=None if model.pause_until is None else parse_utc(model.pause_until),
        updated_at=parse_utc(model.updated_at),
    )


def _exclusion_rule_to_model(record: ExclusionRule) -> ExclusionRuleModel:
    return ExclusionRuleModel(
        id=str(record.id),
        rule_type=record.rule_type.value,
        pattern=record.pattern,
        scope=record.scope.value,
        enabled=record.enabled,
        built_in=record.built_in,
        created_at=format_utc(record.created_at),
        updated_at=format_utc(record.updated_at),
    )


def _exclusion_rule_from_model(model: ExclusionRuleModel) -> ExclusionRule:
    return ExclusionRule(
        id=UUID(model.id),
        rule_type=ExclusionRuleType(model.rule_type),
        pattern=model.pattern,
        scope=ExclusionScope(model.scope),
        enabled=model.enabled,
        built_in=model.built_in,
        created_at=parse_utc(model.created_at),
        updated_at=parse_utc(model.updated_at),
    )


def _observation_from_model(model: ObservationModel) -> Observation:
    return Observation(
        id=UUID(model.id),
        idempotency_key=model.idempotency_key,
        source_type=SourceType(model.source_type),
        activity_state=ActivityState(model.activity_state),
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
        expires_at=parse_utc(model.expires_at),
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
        lineage_key=record.lineage_key,
        type=record.type,
        summary=record.summary,
        facts=record.facts,
        started_at=format_utc(record.started_at),
        ended_at=format_utc(record.ended_at),
        valid_from=format_utc(record.valid_from),
        valid_until=(
            None if record.valid_until is None else format_utc(record.valid_until)
        ),
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
        lineage_key=model.lineage_key,
        type=_persisted_event_type(model.type),
        summary=model.summary,
        facts=model.facts,
        started_at=parse_utc(model.started_at),
        ended_at=parse_utc(model.ended_at),
        valid_from=parse_utc(model.valid_from),
        valid_until=None if model.valid_until is None else parse_utc(model.valid_until),
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


def _event_correction_to_model(record: EventCorrection) -> EventCorrectionModel:
    return EventCorrectionModel(
        id=str(record.id),
        idempotency_key=record.idempotency_key,
        event_lineage_key=record.event_lineage_key,
        target_event_id=str(record.target_event_id),
        replacement=record.replacement.model_dump(mode="json"),
        reason=record.reason,
        supersedes_correction_id=(
            None
            if record.supersedes_correction_id is None
            else str(record.supersedes_correction_id)
        ),
        created_at=format_utc(record.created_at),
    )


def _event_correction_from_model(model: EventCorrectionModel) -> EventCorrection:
    replacement = EventCorrectionContent.model_validate_json(
        json.dumps(model.replacement, ensure_ascii=False)
    )
    return EventCorrection(
        id=UUID(model.id),
        idempotency_key=model.idempotency_key,
        event_lineage_key=model.event_lineage_key,
        target_event_id=UUID(model.target_event_id),
        replacement=replacement,
        reason=model.reason,
        supersedes_correction_id=(
            None
            if model.supersedes_correction_id is None
            else UUID(model.supersedes_correction_id)
        ),
        created_at=parse_utc(model.created_at),
    )


def _timeline_build_to_model(record: TimelineBuild) -> TimelineBuildModel:
    return TimelineBuildModel(
        processing_run_id=str(record.processing_run_id),
        processing_version=record.processing_version,
        window_start=format_utc(record.window_start),
        window_end=format_utc(record.window_end),
        session_gap_seconds=record.session_gap_seconds,
        max_session_duration_seconds=record.max_session_duration_seconds,
    )


def _timeline_build_from_model(model: TimelineBuildModel) -> TimelineBuild:
    return TimelineBuild(
        processing_run_id=UUID(model.processing_run_id),
        processing_version=model.processing_version,
        window_start=parse_utc(model.window_start),
        window_end=parse_utc(model.window_end),
        session_gap_seconds=model.session_gap_seconds,
        max_session_duration_seconds=model.max_session_duration_seconds,
    )


def _persisted_event_type(value: str) -> EventType:
    try:
        return EventType(value)
    except ValueError:
        return EventType.OTHER


def _pattern_to_model(record: Pattern) -> PatternModel:
    return PatternModel(
        id=str(record.id),
        idempotency_key=record.idempotency_key,
        type=record.type.value,
        summary=record.summary,
        window_start=format_utc(record.window_start),
        window_end=format_utc(record.window_end),
        epistemic_status=record.epistemic_status.value,
        confidence=record.confidence,
        sensitivity=record.sensitivity.value,
        evidence_count=record.evidence_count,
        source_event_ids=[str(value) for value in record.source_event_ids],
        projects=list(record.projects),
        entities=list(record.entities),
        metrics=record.metrics,
        valid_from=format_utc(record.valid_from),
        valid_until=(
            None if record.valid_until is None else format_utc(record.valid_until)
        ),
        status=record.status.value,
        processing_version=record.processing_version,
        created_at=format_utc(record.created_at),
    )


def _pattern_from_model(model: PatternModel) -> Pattern:
    return Pattern(
        id=UUID(model.id),
        idempotency_key=model.idempotency_key,
        type=PatternType(model.type),
        summary=model.summary,
        window_start=parse_utc(model.window_start),
        window_end=parse_utc(model.window_end),
        epistemic_status=EpistemicStatus(model.epistemic_status),
        confidence=model.confidence,
        sensitivity=Sensitivity(model.sensitivity),
        evidence_count=model.evidence_count,
        source_event_ids=tuple(UUID(value) for value in model.source_event_ids),
        projects=tuple(model.projects),
        entities=tuple(model.entities),
        metrics=model.metrics,
        valid_from=parse_utc(model.valid_from),
        valid_until=(
            None if model.valid_until is None else parse_utc(model.valid_until)
        ),
        status=PatternStatus(model.status),
        processing_version=model.processing_version,
        created_at=parse_utc(model.created_at),
    )


def _pattern_build_to_model(record: PatternBuild) -> PatternBuildModel:
    return PatternBuildModel(
        processing_run_id=str(record.processing_run_id),
        source_timeline_run_id=str(record.source_timeline_run_id),
        processing_version=record.processing_version,
        comparison_boundary=format_utc(record.comparison_boundary),
        min_project_events=record.min_project_events,
        resumption_gap_seconds=record.resumption_gap_seconds,
        change_ratio=record.change_ratio,
    )


def _pattern_build_from_model(model: PatternBuildModel) -> PatternBuild:
    return PatternBuild(
        processing_run_id=UUID(model.processing_run_id),
        source_timeline_run_id=UUID(model.source_timeline_run_id),
        processing_version=model.processing_version,
        comparison_boundary=parse_utc(model.comparison_boundary),
        min_project_events=model.min_project_events,
        resumption_gap_seconds=model.resumption_gap_seconds,
        change_ratio=model.change_ratio,
    )


def _candidate_build_to_model(record: CandidateBuild) -> CandidateBuildModel:
    return CandidateBuildModel(
        processing_run_id=str(record.processing_run_id),
        source_pattern_run_id=str(record.source_pattern_run_id),
        processing_version=record.processing_version,
        scoring_weights=record.scoring_weights,
    )


def _candidate_build_from_model(model: CandidateBuildModel) -> CandidateBuild:
    return CandidateBuild(
        processing_run_id=UUID(model.processing_run_id),
        source_pattern_run_id=UUID(model.source_pattern_run_id),
        processing_version=model.processing_version,
        scoring_weights=model.scoring_weights,
    )


def _candidate_to_model(record: MemoryCandidate) -> MemoryCandidateModel:
    return MemoryCandidateModel(
        id=str(record.id),
        idempotency_key=record.idempotency_key,
        text=record.text,
        source_type=record.source_type,
        source_ids=[str(item) for item in record.source_ids],
        utility=record.utility,
        importance=record.importance,
        durability=record.durability,
        novelty=record.novelty,
        recurrence=record.recurrence,
        confidence=record.confidence,
        ambiguity=record.ambiguity,
        redundancy=record.redundancy,
        sensitivity=record.sensitivity.value,
        score=record.score,
        scoring_version=record.scoring_version,
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
        utility=model.utility,
        importance=model.importance,
        durability=model.durability,
        novelty=model.novelty,
        recurrence=model.recurrence,
        confidence=model.confidence,
        ambiguity=model.ambiguity,
        redundancy=model.redundancy,
        sensitivity=Sensitivity(model.sensitivity),
        score=model.score,
        scoring_version=model.scoring_version,
        status=CandidateStatus(model.status),
        rejection_reason=model.rejection_reason,
        created_at=parse_utc(model.created_at),
        processed_at=None
        if model.processed_at is None
        else parse_utc(model.processed_at),
    )


def _candidate_decision_to_model(
    record: CandidateDecision,
) -> CandidateDecisionModel:
    return CandidateDecisionModel(
        id=str(record.id),
        idempotency_key=record.idempotency_key,
        candidate_id=str(record.candidate_id),
        processing_run_id=str(record.processing_run_id),
        policy_version=record.policy_version,
        acceptance_threshold=record.acceptance_threshold,
        status=record.status.value,
        reason=record.reason,
        created_at=format_utc(record.created_at),
    )


def _candidate_decision_from_model(
    model: CandidateDecisionModel,
) -> CandidateDecision:
    return CandidateDecision(
        id=UUID(model.id),
        idempotency_key=model.idempotency_key,
        candidate_id=UUID(model.candidate_id),
        processing_run_id=UUID(model.processing_run_id),
        policy_version=model.policy_version,
        acceptance_threshold=model.acceptance_threshold,
        status=CandidateDecisionStatus(model.status),
        reason=model.reason,
        created_at=parse_utc(model.created_at),
    )


def _candidate_evaluation_build_to_model(
    record: CandidateEvaluationBuild,
) -> CandidateEvaluationBuildModel:
    return CandidateEvaluationBuildModel(
        processing_run_id=str(record.processing_run_id),
        source_candidate_run_id=str(record.source_candidate_run_id),
        policy_version=record.policy_version,
        acceptance_threshold=record.acceptance_threshold,
        minimum_confidence=record.minimum_confidence,
        maximum_ambiguity=record.maximum_ambiguity,
        maximum_redundancy=record.maximum_redundancy,
    )


def _candidate_evaluation_build_from_model(
    model: CandidateEvaluationBuildModel,
) -> CandidateEvaluationBuild:
    return CandidateEvaluationBuild(
        processing_run_id=UUID(model.processing_run_id),
        source_candidate_run_id=UUID(model.source_candidate_run_id),
        policy_version=model.policy_version,
        acceptance_threshold=model.acceptance_threshold,
        minimum_confidence=model.minimum_confidence,
        maximum_ambiguity=model.maximum_ambiguity,
        maximum_redundancy=model.maximum_redundancy,
    )


def _memory_correction_build_to_model(
    record: MemoryCorrectionBuild,
) -> MemoryCorrectionBuildModel:
    return MemoryCorrectionBuildModel(
        candidate_id=str(record.candidate_id),
        target_memory_id=str(record.target_memory_id),
        provider=record.provider,
        endpoint=record.endpoint,
        model=record.model,
        model_digest=record.model_digest,
        prompt_version=record.prompt_version,
        output_schema_version=record.output_schema_version,
        replacement_sha256=record.replacement_sha256,
        started_at=format_utc(record.started_at),
        ended_at=format_utc(record.ended_at),
        wall_duration_ms=record.wall_duration_ms,
    )


def _memory_correction_build_from_model(
    model: MemoryCorrectionBuildModel,
) -> MemoryCorrectionBuild:
    if model.provider != "ollama":
        raise DatabaseError("Memory correction provider is invalid")
    return MemoryCorrectionBuild(
        candidate_id=UUID(model.candidate_id),
        target_memory_id=UUID(model.target_memory_id),
        provider="ollama",
        endpoint=model.endpoint,
        model=model.model,
        model_digest=model.model_digest,
        prompt_version=model.prompt_version,
        output_schema_version=model.output_schema_version,
        replacement_sha256=model.replacement_sha256,
        started_at=parse_utc(model.started_at),
        ended_at=parse_utc(model.ended_at),
        wall_duration_ms=model.wall_duration_ms,
    )


def _agent_proposal_adoption_build_to_model(
    record: AgentProposalAdoptionBuild,
) -> AgentProposalAdoptionBuildModel:
    return AgentProposalAdoptionBuildModel(
        proposal_id=str(record.proposal_id),
        candidate_id=(
            None if record.candidate_id is None else str(record.candidate_id)
        ),
        provider=record.provider,
        endpoint=record.endpoint,
        model=record.model,
        model_digest=record.model_digest,
        prompt_version=record.prompt_version,
        output_schema_version=record.output_schema_version,
        decision=record.decision.value,
        reason_code=record.reason_code.value,
        confidence=record.confidence,
        reference_sha256=record.reference_sha256,
        active_memory_sha256=record.active_memory_sha256,
        active_memory_count=record.active_memory_count,
        started_at=format_utc(record.started_at),
        ended_at=format_utc(record.ended_at),
        wall_duration_ms=record.wall_duration_ms,
    )


def _agent_proposal_adoption_build_from_model(
    model: AgentProposalAdoptionBuildModel,
) -> AgentProposalAdoptionBuild:
    if model.provider != "ollama":
        raise DatabaseError("Agent proposal adoption provider is invalid")
    return AgentProposalAdoptionBuild(
        proposal_id=UUID(model.proposal_id),
        candidate_id=(None if model.candidate_id is None else UUID(model.candidate_id)),
        provider="ollama",
        endpoint=model.endpoint,
        model=model.model,
        model_digest=model.model_digest,
        prompt_version=model.prompt_version,
        output_schema_version=model.output_schema_version,
        decision=AgentProposalDecision(model.decision),
        reason_code=AgentProposalReasonCode(model.reason_code),
        confidence=model.confidence,
        reference_sha256=model.reference_sha256,
        active_memory_sha256=model.active_memory_sha256,
        active_memory_count=model.active_memory_count,
        started_at=parse_utc(model.started_at),
        ended_at=parse_utc(model.ended_at),
        wall_duration_ms=model.wall_duration_ms,
    )


def _memory_promotion_build_to_model(
    record: MemoryPromotionBuild,
) -> MemoryPromotionBuildModel:
    return MemoryPromotionBuildModel(
        processing_run_id=str(record.processing_run_id),
        source_evaluation_run_id=str(record.source_evaluation_run_id),
        processing_version=record.processing_version,
    )


def _memory_promotion_build_from_model(
    model: MemoryPromotionBuildModel,
) -> MemoryPromotionBuild:
    return MemoryPromotionBuild(
        processing_run_id=UUID(model.processing_run_id),
        source_evaluation_run_id=UUID(model.source_evaluation_run_id),
        processing_version=model.processing_version,
    )


def _agent_proposal_to_model(record: AgentProposal) -> AgentProposalModel:
    return AgentProposalModel(
        id=str(record.id),
        idempotency_key=record.idempotency_key,
        agent_id=record.agent_id,
        agent_role=record.agent_role.value,
        text=record.text,
        proposal_type=record.proposal_type.value,
        reference_type=(
            None if record.reference_type is None else record.reference_type.value
        ),
        reference_id=(
            None if record.reference_id is None else str(record.reference_id)
        ),
        status=record.status.value,
        reason=record.reason,
        created_at=format_utc(record.created_at),
        processed_at=(
            None if record.processed_at is None else format_utc(record.processed_at)
        ),
    )


def _agent_proposal_from_model(model: AgentProposalModel) -> AgentProposal:
    return AgentProposal(
        id=UUID(model.id),
        idempotency_key=model.idempotency_key,
        agent_id=model.agent_id,
        agent_role=AgentRole(model.agent_role),
        text=model.text,
        proposal_type=AgentProposalType(model.proposal_type),
        reference_type=(
            None
            if model.reference_type is None
            else ProposalReferenceType(model.reference_type)
        ),
        reference_id=(None if model.reference_id is None else UUID(model.reference_id)),
        status=AgentProposalStatus(model.status),
        reason=model.reason,
        created_at=parse_utc(model.created_at),
        processed_at=(
            None if model.processed_at is None else parse_utc(model.processed_at)
        ),
    )


def _candidate_identity(record: MemoryCandidate) -> dict[str, object]:
    return record.model_dump(
        exclude={"status", "rejection_reason", "processed_at"},
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
        candidate_decision_id=(
            None
            if record.candidate_decision_id is None
            else str(record.candidate_decision_id)
        ),
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
        candidate_decision_id=(
            None
            if model.candidate_decision_id is None
            else UUID(model.candidate_decision_id)
        ),
        provenance=MemoryProvenance(
            candidate_id=UUID(str(provenance_data["candidate_id"])),
            pattern_ids=tuple(
                UUID(item) for item in provenance_data.get("pattern_ids", ())
            ),
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


def _processing_run_from_model(model: ProcessingRunModel) -> ProcessingRun:
    return ProcessingRun(
        id=UUID(model.id),
        pipeline=model.pipeline,
        version=model.version,
        started_at=parse_utc(model.started_at),
        ended_at=None if model.ended_at is None else parse_utc(model.ended_at),
        status=ProcessingRunStatus(model.status),
        input_count=model.input_count,
        output_count=model.output_count,
        error_code=model.error_code,
        error_summary=model.error_summary,
    )


def _model_transformation_to_model(
    record: ModelTransformation,
) -> ModelTransformationModel:
    model = ModelTransformationModel(id=str(record.id))
    _update_model_transformation(model, record)
    return model


def _update_model_transformation(
    model: ModelTransformationModel,
    record: ModelTransformation,
) -> None:
    model.idempotency_key = record.idempotency_key
    model.source_observation_ids = [
        str(value) for value in record.source_observation_ids
    ]
    model.provider = record.provider
    model.endpoint = record.endpoint
    model.configured_model = record.configured_model
    model.runtime_version = record.runtime_version
    model.resolved_model = record.resolved_model
    model.model_digest = record.model_digest
    model.prompt_version = record.prompt_version
    model.output_schema_version = record.output_schema_version
    model.image_sha256 = record.image_sha256
    model.status = record.status.value
    model.interpretation = (
        None
        if record.interpretation is None
        else record.interpretation.model_dump(mode="json")
    )
    model.sensitivity = (
        None
        if record.interpretation is None
        else record.interpretation.sensitivity.value
    )
    model.attempt_count = record.attempt_count
    model.started_at = (
        None if record.started_at is None else format_utc(record.started_at)
    )
    model.ended_at = None if record.ended_at is None else format_utc(record.ended_at)
    model.wall_duration_ms = record.wall_duration_ms
    model.runtime_duration_ms = record.runtime_duration_ms
    model.load_duration_ms = record.load_duration_ms
    model.prompt_eval_count = record.prompt_eval_count
    model.eval_count = record.eval_count
    model.next_attempt_at = (
        None if record.next_attempt_at is None else format_utc(record.next_attempt_at)
    )
    model.last_error_code = record.last_error_code
    model.created_at = format_utc(record.created_at)
    model.updated_at = format_utc(record.updated_at)


def _model_transformation_from_model(
    model: ModelTransformationModel,
) -> ModelTransformation:
    observation_ids = tuple(UUID(value) for value in model.source_observation_ids)
    interpretation = (
        None
        if model.interpretation is None
        else ModelInterpretation.model_validate_json(
            json.dumps(model.interpretation, ensure_ascii=False),
            strict=True,
        )
    )
    return ModelTransformation.model_validate(
        {
            "id": UUID(model.id),
            "idempotency_key": model.idempotency_key,
            "source_observation_ids": observation_ids,
            "provider": model.provider,
            "endpoint": model.endpoint,
            "configured_model": model.configured_model,
            "runtime_version": model.runtime_version,
            "resolved_model": model.resolved_model,
            "model_digest": model.model_digest,
            "prompt_version": model.prompt_version,
            "output_schema_version": model.output_schema_version,
            "image_sha256": model.image_sha256,
            "status": ModelTransformationStatus(model.status),
            "interpretation": interpretation,
            "attempt_count": model.attempt_count,
            "started_at": (
                None if model.started_at is None else parse_utc(model.started_at)
            ),
            "ended_at": (None if model.ended_at is None else parse_utc(model.ended_at)),
            "wall_duration_ms": model.wall_duration_ms,
            "runtime_duration_ms": model.runtime_duration_ms,
            "load_duration_ms": model.load_duration_ms,
            "prompt_eval_count": model.prompt_eval_count,
            "eval_count": model.eval_count,
            "next_attempt_at": (
                None
                if model.next_attempt_at is None
                else parse_utc(model.next_attempt_at)
            ),
            "last_error_code": model.last_error_code,
            "created_at": parse_utc(model.created_at),
            "updated_at": parse_utc(model.updated_at),
        }
    )


def _model_attempt_to_model(record: ModelAttempt) -> ModelAttemptModel:
    return ModelAttemptModel(
        transformation_id=str(record.transformation_id),
        attempt_number=record.attempt_number,
        processing_run_id=str(record.processing_run_id),
        invocation=record.invocation.value,
        status=record.status.value,
        error_code=record.error_code,
        started_at=format_utc(record.started_at),
        ended_at=format_utc(record.ended_at),
    )


def _model_attempt_from_model(model: ModelAttemptModel) -> ModelAttempt:
    return ModelAttempt(
        transformation_id=UUID(model.transformation_id),
        processing_run_id=UUID(model.processing_run_id),
        attempt_number=model.attempt_number,
        invocation=ModelAttemptInvocation(model.invocation),
        status=ModelTransformationStatus(model.status),
        error_code=model.error_code,
        started_at=parse_utc(model.started_at),
        ended_at=parse_utc(model.ended_at),
    )


def _require_same_transformation_identity(
    existing: ModelTransformation,
    replayed: ModelTransformation,
) -> None:
    if (
        existing.idempotency_key,
        existing.source_observation_ids,
        existing.provider,
        existing.endpoint,
        existing.configured_model,
        existing.prompt_version,
        existing.output_schema_version,
        existing.image_sha256,
    ) != (
        replayed.idempotency_key,
        replayed.source_observation_ids,
        replayed.provider,
        replayed.endpoint,
        replayed.configured_model,
        replayed.prompt_version,
        replayed.output_schema_version,
        replayed.image_sha256,
    ):
        raise DatabaseError("Model transformation idempotency key conflicts")


def _optional_model_configuration(
    provider: str | None,
    endpoint: str | None,
    configured_model: str | None,
    prompt_version: str | None,
    output_schema_version: str | None,
) -> tuple[str, str, str, str, str] | None:
    values = (
        provider,
        endpoint,
        configured_model,
        prompt_version,
        output_schema_version,
    )
    if all(value is None for value in values):
        return None
    if any(value is None for value in values):
        raise ValueError("model configuration filters must be provided together")
    return (
        str(provider),
        str(endpoint),
        str(configured_model),
        str(prompt_version),
        str(output_schema_version),
    )


def _validate_transformation_transition(
    existing: ModelTransformation,
    updated: ModelTransformation,
) -> None:
    _require_same_transformation_identity(existing, updated)
    if existing == updated:
        return
    allowed = (
        (
            existing.status is ModelTransformationStatus.PENDING
            and updated.status is ModelTransformationStatus.RUNNING
        )
        or (
            existing.status is ModelTransformationStatus.RUNNING
            and updated.status
            in {
                ModelTransformationStatus.SUCCEEDED,
                ModelTransformationStatus.FAILED,
                ModelTransformationStatus.ABANDONED,
            }
        )
        or (
            existing.status is ModelTransformationStatus.FAILED
            and updated.status
            in {
                ModelTransformationStatus.RUNNING,
                ModelTransformationStatus.ABANDONED,
            }
        )
    )
    if not allowed:
        raise DatabaseError("Invalid model transformation state transition")
