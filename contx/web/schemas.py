"""Explicit versioned API schemas for the local CONTX interface."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from contx.application import (
    ActivityInspection,
    AgentInspection,
    MemoryInspection,
    OverviewInspection,
    PrivacyInspection,
)
from contx.model_provider import ModelInterpretation, ModelTransformation
from contx.models import (
    AgentProposal,
    AgentProposalAdoptionBuild,
    CandidateDecision,
    Event,
    EventCorrection,
    ExclusionRule,
    ExclusionRuleType,
    MemoryCandidate,
    MemoryCorrectionBuild,
    MemoryLink,
    Observation,
    Pattern,
    ProcessingRun,
)
from contx.settings import AppSettings

API_VERSION = "v1"


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EmptyRequest(ApiModel):
    pass


class PauseRequest(ApiModel):
    duration_minutes: int | None = Field(default=None, ge=1, le=43_200)


class ExclusionCreateRequest(ApiModel):
    rule_type: ExclusionRuleType
    pattern: str = Field(min_length=1, max_length=255)


class ExclusionUpdateRequest(ApiModel):
    enabled: bool


class ImmediateRawPurgeRequest(ApiModel):
    confirmation: Literal["DELETE RAW ARTIFACTS"]


class FullDataDeletionRequest(ApiModel):
    confirmation: Literal["DELETE ALL CONTX DATA"]


class MemoryCorrectionRequest(ApiModel):
    replacement: str = Field(min_length=1, max_length=4000)


class EventCorrectionRequest(ApiModel):
    summary: str = Field(min_length=1, max_length=2000)
    reason: str = Field(min_length=1, max_length=500)


class ProposalRejectionRequest(ApiModel):
    reason: str = Field(default="user_rejected", min_length=1, max_length=255)


class CollectionState(ApiModel):
    paused: bool
    paused_at: datetime | None
    pause_until: datetime | None
    updated_at: datetime
    background_enabled: bool
    daemon_running: bool
    daemon_pid: int | None


class CapabilityView(ApiModel):
    name: str
    status: str
    reason_code: str | None
    settings_path: str | None


class LocalModelView(ApiModel):
    provider: str
    endpoint: str
    runtime_available: bool
    runtime_version: str | None
    model: str
    model_available: bool
    model_digest: str | None
    reason_code: str | None


class ProcessingRunView(ApiModel):
    id: UUID
    pipeline: str
    version: str
    started_at: datetime
    ended_at: datetime | None
    status: str
    input_count: int
    output_count: int
    error_code: str | None
    error_summary: str | None

    @classmethod
    def from_record(cls, record: ProcessingRun) -> ProcessingRunView:
        return cls(**record.model_dump(mode="python"))


class OperationalIssueView(ApiModel):
    code: str
    summary: str
    occurred_at: datetime | None


class StatusResponse(ApiModel):
    api_version: Literal["v1"] = "v1"
    product_version: str
    health: Literal["healthy", "degraded"]
    collection: CollectionState
    capabilities: tuple[CapabilityView, ...]
    model: LocalModelView
    counts: dict[str, int]
    raw_usage_bytes: int
    memory_usage_bytes: int
    model_backlog: int
    model_event_backlog: int
    abandoned_transformations: int
    last_processing_run: ProcessingRunView | None
    issues: tuple[OperationalIssueView, ...]

    @classmethod
    def from_inspection(
        cls,
        inspection: OverviewInspection,
        *,
        product_version: str,
        background_enabled: bool,
        now: datetime,
    ) -> StatusResponse:
        return cls(
            product_version=product_version,
            health="degraded" if inspection.issues else "healthy",
            collection=CollectionState(
                paused=inspection.collection.is_paused(at=now),
                paused_at=inspection.collection.paused_at,
                pause_until=inspection.collection.pause_until,
                updated_at=inspection.collection.updated_at,
                background_enabled=background_enabled,
                daemon_running=inspection.daemon.running,
                daemon_pid=inspection.daemon.pid,
            ),
            capabilities=tuple(
                CapabilityView(
                    name=item.name,
                    status=item.status.value,
                    reason_code=item.reason_code,
                    settings_path=item.settings_path,
                )
                for item in inspection.capabilities
            ),
            model=LocalModelView(**inspection.model.model_dump(mode="python")),
            counts=inspection.counts,
            raw_usage_bytes=inspection.raw_usage_bytes,
            memory_usage_bytes=inspection.memory_usage_bytes,
            model_backlog=inspection.model_backlog,
            model_event_backlog=inspection.model_event_backlog,
            abandoned_transformations=inspection.abandoned_transformations,
            last_processing_run=(
                None
                if inspection.last_processing_run is None
                else ProcessingRunView.from_record(inspection.last_processing_run)
            ),
            issues=tuple(
                OperationalIssueView(
                    code=item.code,
                    summary=item.summary,
                    occurred_at=item.occurred_at,
                )
                for item in inspection.issues
            ),
        )


class CollectionResponse(ApiModel):
    paused: bool
    paused_at: datetime | None
    pause_until: datetime | None
    updated_at: datetime


class ExclusionView(ApiModel):
    id: UUID
    rule_type: str
    pattern: str
    scope: str
    enabled: bool
    built_in: bool
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_record(cls, record: ExclusionRule) -> ExclusionView:
        return cls(**record.model_dump(mode="json"))


class ObservationView(ApiModel):
    id: UUID
    source_type: str
    activity_state: str
    captured_at: datetime
    started_at: datetime | None
    ended_at: datetime | None
    app_name: str | None
    app_bundle_id: str | None
    window_title: str | None
    excluded: bool
    exclusion_reason: str | None
    processing_status: str
    expires_at: datetime
    has_raw_artifact: bool

    @classmethod
    def from_record(cls, record: Observation) -> ObservationView:
        return cls(
            id=record.id,
            source_type=record.source_type.value,
            activity_state=record.activity_state.value,
            captured_at=record.captured_at,
            started_at=record.started_at,
            ended_at=record.ended_at,
            app_name=record.app_name,
            app_bundle_id=record.app_bundle_id,
            window_title=record.window_title,
            excluded=record.excluded,
            exclusion_reason=record.exclusion_reason,
            processing_status=record.processing_status.value,
            expires_at=record.expires_at,
            has_raw_artifact=record.artifact_path is not None,
        )


class EventView(ApiModel):
    id: UUID
    lineage_key: str
    type: str
    summary: str
    facts: dict[str, Any]
    started_at: datetime
    ended_at: datetime
    valid_from: datetime
    valid_until: datetime | None
    epistemic_status: str
    confidence: float
    sensitivity: str
    projects: tuple[str, ...]
    entities: tuple[str, ...]
    source_observation_ids: tuple[UUID, ...]
    processing_version: str

    @classmethod
    def from_record(cls, record: Event) -> EventView:
        return cls(
            id=record.id,
            lineage_key=record.lineage_key,
            type=record.type.value,
            summary=record.summary,
            facts=record.facts,
            started_at=record.started_at,
            ended_at=record.ended_at,
            valid_from=record.valid_from,
            valid_until=record.valid_until,
            epistemic_status=record.epistemic_status.value,
            confidence=record.confidence,
            sensitivity=record.sensitivity.value,
            projects=record.projects,
            entities=record.entities,
            source_observation_ids=record.source_observation_ids,
            processing_version=record.processing_version,
        )


class EventCorrectionView(ApiModel):
    id: UUID
    target_event_id: UUID
    event_lineage_key: str
    summary: str
    reason: str
    supersedes_correction_id: UUID | None
    created_at: datetime

    @classmethod
    def from_record(cls, record: EventCorrection) -> EventCorrectionView:
        return cls(
            id=record.id,
            target_event_id=record.target_event_id,
            event_lineage_key=record.event_lineage_key,
            summary=record.replacement.summary,
            reason=record.reason,
            supersedes_correction_id=record.supersedes_correction_id,
            created_at=record.created_at,
        )


class ActivityResponse(ApiModel):
    observations: tuple[ObservationView, ...]
    events: tuple[EventView, ...]
    corrections: tuple[EventCorrectionView, ...]

    @classmethod
    def from_inspection(cls, inspection: ActivityInspection) -> ActivityResponse:
        return cls(
            observations=tuple(
                ObservationView.from_record(item) for item in inspection.observations
            ),
            events=tuple(EventView.from_record(item) for item in inspection.events),
            corrections=tuple(
                EventCorrectionView.from_record(item)
                for item in inspection.event_corrections
            ),
        )


class PatternView(ApiModel):
    id: UUID
    type: str
    summary: str
    window_start: datetime
    window_end: datetime
    confidence: float
    sensitivity: str
    evidence_count: int
    source_event_ids: tuple[UUID, ...]
    projects: tuple[str, ...]
    metrics: dict[str, float | int | str]
    valid_from: datetime
    valid_until: datetime | None
    status: str

    @classmethod
    def from_record(cls, record: Pattern, *, now: datetime) -> PatternView:
        return cls(
            id=record.id,
            type=record.type.value,
            summary=record.summary,
            window_start=record.window_start,
            window_end=record.window_end,
            confidence=record.confidence,
            sensitivity=record.sensitivity.value,
            evidence_count=record.evidence_count,
            source_event_ids=record.source_event_ids,
            projects=record.projects,
            metrics=record.metrics,
            valid_from=record.valid_from,
            valid_until=record.valid_until,
            status=record.status_at(now).value,
        )


class PatternsResponse(ApiModel):
    patterns: tuple[PatternView, ...]


class CandidateView(ApiModel):
    id: UUID
    text: str
    source_type: str
    source_ids: tuple[UUID, ...]
    confidence: float
    sensitivity: str
    score: float
    status: str
    rejection_reason: str | None
    created_at: datetime
    processed_at: datetime | None

    @classmethod
    def from_record(cls, record: MemoryCandidate) -> CandidateView:
        return cls(
            id=record.id,
            text=record.text,
            source_type=record.source_type,
            source_ids=record.source_ids,
            confidence=record.confidence,
            sensitivity=record.sensitivity.value,
            score=record.score,
            status=record.status.value,
            rejection_reason=record.rejection_reason,
            created_at=record.created_at,
            processed_at=record.processed_at,
        )


class MemoryRecordView(ApiModel):
    id: UUID
    backend_id: str
    candidate_id: UUID
    text: str
    confidence: float
    status: str
    supersedes_memory_id: UUID | None
    created_at: datetime
    pattern_ids: tuple[UUID, ...]
    event_ids: tuple[UUID, ...]
    observation_ids: tuple[UUID, ...]

    @classmethod
    def from_record(
        cls, link: MemoryLink, candidate: MemoryCandidate
    ) -> MemoryRecordView:
        return cls(
            id=link.id,
            backend_id=link.memory_backend_id,
            candidate_id=link.candidate_id,
            text=candidate.text,
            confidence=link.confidence,
            status=link.status.value,
            supersedes_memory_id=link.supersedes_memory_id,
            created_at=link.created_at,
            pattern_ids=link.provenance.pattern_ids,
            event_ids=link.provenance.event_ids,
            observation_ids=link.provenance.observation_ids,
        )


class MemoryCorrectionView(ApiModel):
    candidate_id: UUID
    target_memory_id: UUID
    provider: str
    model: str
    model_digest: str
    prompt_version: str
    output_schema_version: str
    started_at: datetime
    ended_at: datetime
    wall_duration_ms: int

    @classmethod
    def from_record(cls, record: MemoryCorrectionBuild) -> MemoryCorrectionView:
        return cls(
            candidate_id=record.candidate_id,
            target_memory_id=record.target_memory_id,
            provider=record.provider,
            model=record.model,
            model_digest=record.model_digest,
            prompt_version=record.prompt_version,
            output_schema_version=record.output_schema_version,
            started_at=record.started_at,
            ended_at=record.ended_at,
            wall_duration_ms=record.wall_duration_ms,
        )


class MemoryResponse(ApiModel):
    candidates: tuple[CandidateView, ...]
    memories: tuple[MemoryRecordView, ...]
    corrections: tuple[MemoryCorrectionView, ...]

    @classmethod
    def from_inspection(cls, inspection: MemoryInspection) -> MemoryResponse:
        return cls(
            candidates=tuple(
                CandidateView.from_record(item) for item in inspection.candidates
            ),
            memories=tuple(
                MemoryRecordView.from_record(link, candidate)
                for link, candidate in inspection.records
            ),
            corrections=tuple(
                MemoryCorrectionView.from_record(item)
                for item in inspection.corrections
            ),
        )


class WakeResponse(ApiModel):
    content: str
    complete: bool
    maintenance_required: bool
    snapshot: int | None
    next_part: int | None
    active_memory_count: int
    projection_generation: str


class RawPurgeResponse(ApiModel):
    processing_run_id: UUID
    status: str
    purged_observation_ids: tuple[UUID, ...]
    failed_observation_ids: tuple[UUID, ...]
    bytes_reclaimed: int
    orphan_artifacts_deleted: int


class FullDataDeletionResponse(ApiModel):
    removed_stores: tuple[str, ...]
    service_state: Literal["data_deleted"] = "data_deleted"


class MemoryCorrectionResponse(ApiModel):
    memory_id: UUID
    candidate_id: UUID
    superseded_memory_id: UUID
    replayed: bool
    maintenance_required: bool


class EventCorrectionResponse(ApiModel):
    correction_id: UUID
    event_id: UUID
    supersedes_correction_id: UUID | None
    created_at: datetime


class ProposalDecisionResponse(ApiModel):
    proposal_id: UUID
    status: str
    decision: str | None = None
    reason: str | None = None
    memory_id: UUID | None = None
    replayed: bool = False


class RawArtifactView(ApiModel):
    observation_id: UUID
    source_type: str
    captured_at: datetime
    expires_at: datetime
    size_bytes: int
    available: bool


class InterpretationView(ApiModel):
    summary: str
    activity_type: str
    observed_facts: tuple[str, ...]
    inferred_context: tuple[str, ...]
    projects: tuple[str, ...]
    entities: tuple[str, ...]
    sensitivity: str
    sensitive_categories: tuple[str, ...]
    confidence: float
    memory_relevance: float

    @classmethod
    def from_record(cls, record: ModelInterpretation) -> InterpretationView:
        return cls(
            summary=record.summary,
            activity_type=record.activity_type,
            observed_facts=record.observed_facts,
            inferred_context=record.inferred_context,
            projects=record.projects,
            entities=record.entities,
            sensitivity=record.sensitivity.value,
            sensitive_categories=tuple(
                item.value for item in record.sensitive_categories
            ),
            confidence=record.confidence,
            memory_relevance=record.memory_relevance,
        )


class TransformationView(ApiModel):
    id: UUID
    source_observation_ids: tuple[UUID, ...]
    provider: str
    endpoint: str
    configured_model: str
    runtime_version: str | None
    resolved_model: str | None
    model_digest: str | None
    prompt_version: str
    output_schema_version: str
    image_sha256: str
    status: str
    interpretation: InterpretationView | None
    attempt_count: int
    started_at: datetime | None
    ended_at: datetime | None
    wall_duration_ms: int | None
    last_error_code: str | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_record(cls, record: ModelTransformation) -> TransformationView:
        return cls(
            id=record.id,
            source_observation_ids=record.source_observation_ids,
            provider=record.provider,
            endpoint=record.endpoint,
            configured_model=record.configured_model,
            runtime_version=record.runtime_version,
            resolved_model=record.resolved_model,
            model_digest=record.model_digest,
            prompt_version=record.prompt_version,
            output_schema_version=record.output_schema_version,
            image_sha256=record.image_sha256,
            status=record.status.value,
            interpretation=(
                None
                if record.interpretation is None
                else InterpretationView.from_record(record.interpretation)
            ),
            attempt_count=record.attempt_count,
            started_at=record.started_at,
            ended_at=record.ended_at,
            wall_duration_ms=record.wall_duration_ms,
            last_error_code=record.last_error_code,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )


class CandidateDecisionView(ApiModel):
    id: UUID
    candidate_id: UUID
    processing_run_id: UUID
    policy_version: str
    status: str
    reason: str | None
    created_at: datetime

    @classmethod
    def from_record(cls, record: CandidateDecision) -> CandidateDecisionView:
        return cls(
            id=record.id,
            candidate_id=record.candidate_id,
            processing_run_id=record.processing_run_id,
            policy_version=record.policy_version,
            status=record.status.value,
            reason=record.reason,
            created_at=record.created_at,
        )


class PrivacyResponse(ApiModel):
    raw_artifacts: tuple[RawArtifactView, ...]
    transformations: tuple[TransformationView, ...]
    candidate_decisions: tuple[CandidateDecisionView, ...]

    @classmethod
    def from_inspection(cls, inspection: PrivacyInspection) -> PrivacyResponse:
        return cls(
            raw_artifacts=tuple(
                RawArtifactView(
                    observation_id=item.observation_id,
                    source_type=item.source_type,
                    captured_at=item.captured_at,
                    expires_at=item.expires_at,
                    size_bytes=item.size_bytes,
                    available=item.available,
                )
                for item in inspection.raw_artifacts
            ),
            transformations=tuple(
                TransformationView.from_record(item)
                for item in inspection.transformations
            ),
            candidate_decisions=tuple(
                CandidateDecisionView.from_record(item)
                for item in inspection.candidate_decisions
            ),
        )


class AgentProposalView(ApiModel):
    id: UUID
    agent_id: str
    agent_role: str
    text: str
    reference_type: str | None
    reference_id: UUID | None
    status: str
    reason: str | None
    created_at: datetime
    processed_at: datetime | None

    @classmethod
    def from_record(cls, record: AgentProposal) -> AgentProposalView:
        return cls(
            id=record.id,
            agent_id=record.agent_id,
            agent_role=record.agent_role.value,
            text=record.text,
            reference_type=(
                None if record.reference_type is None else record.reference_type.value
            ),
            reference_id=record.reference_id,
            status=record.status.value,
            reason=record.reason,
            created_at=record.created_at,
            processed_at=record.processed_at,
        )


class ProposalValidationView(ApiModel):
    proposal_id: UUID
    candidate_id: UUID | None
    provider: str
    model: str
    model_digest: str
    prompt_version: str
    output_schema_version: str
    decision: str
    reason_code: str
    confidence: float
    active_memory_count: int
    started_at: datetime
    ended_at: datetime
    wall_duration_ms: int

    @classmethod
    def from_record(cls, record: AgentProposalAdoptionBuild) -> ProposalValidationView:
        return cls(
            proposal_id=record.proposal_id,
            candidate_id=record.candidate_id,
            provider=record.provider,
            model=record.model,
            model_digest=record.model_digest,
            prompt_version=record.prompt_version,
            output_schema_version=record.output_schema_version,
            decision=record.decision.value,
            reason_code=record.reason_code.value,
            confidence=record.confidence,
            active_memory_count=record.active_memory_count,
            started_at=record.started_at,
            ended_at=record.ended_at,
            wall_duration_ms=record.wall_duration_ms,
        )


class AgentResponse(ApiModel):
    proposals: tuple[AgentProposalView, ...]
    validations: tuple[ProposalValidationView, ...]

    @classmethod
    def from_inspection(cls, inspection: AgentInspection) -> AgentResponse:
        return cls(
            proposals=tuple(
                AgentProposalView.from_record(item) for item in inspection.proposals
            ),
            validations=tuple(
                ProposalValidationView.from_record(item)
                for item in inspection.validations
            ),
        )


class ProcessingResponse(ApiModel):
    runs: tuple[ProcessingRunView, ...]


class SettingsResponse(ApiModel):
    config_version: int
    collection: dict[str, Any]
    model: dict[str, Any]
    events: dict[str, Any]
    memory: dict[str, Any]
    processing: dict[str, Any]
    api: dict[str, Any]

    @classmethod
    def from_settings(cls, settings: AppSettings, *, port: int) -> SettingsResponse:
        payload = settings.model_dump(mode="json")
        return cls(
            config_version=settings.config_version,
            collection=payload["collection"],
            model=payload["model"],
            events=payload["events"],
            memory=payload["memory"],
            processing=payload["processing"],
            api={"host": "127.0.0.1", "port": port, "version": API_VERSION},
        )
