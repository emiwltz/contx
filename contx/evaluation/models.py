"""Strict input records for a reproducible 7- to 14-day pilot."""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import StrEnum
from typing import Literal, Self
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from contx.models.common import require_aware_utc


class PilotScenario(StrEnum):
    PROJECT_RESUMPTION = "project_resumption"
    RECENT_UNDERSTANDING = "recent_understanding"
    CHANGE_DETECTION = "change_detection"


class PilotCondition(StrEnum):
    WITHOUT_CONTX = "without_contx"
    WITH_CONTX = "with_contx"


class GroundTruthKind(StrEnum):
    PROJECT_WORK = "project_work"
    PRIORITY_CHANGE = "priority_change"
    DECISION = "decision"
    BLOCKER = "blocker"
    IMPORTANT_EVENT = "important_event"
    EXPECTED_IGNORE = "expected_ignore"
    CORRECTION = "correction"


class GroundTruthImportance(StrEnum):
    CONTEXT = "context"
    IMPORTANT = "important"
    CRITICAL = "critical"

    @property
    def counts_as_important(self) -> bool:
        return self in {self.IMPORTANT, self.CRITICAL}


class Severity(StrEnum):
    INFO = "info"
    MINOR = "minor"
    MAJOR = "major"
    CRITICAL = "critical"


class PrivacyIncidentCategory(StrEnum):
    EXCLUSION_MISS = "exclusion_miss"
    RAW_RETENTION = "raw_retention"
    REMOTE_TRANSPORT = "remote_transport"
    SENSITIVE_PROMOTION = "sensitive_promotion"
    PERMISSION = "permission"
    OTHER = "other"


class ResourcePhase(StrEnum):
    IDLE = "idle"
    ACTIVE_COLLECTION = "active_collection"
    MODEL_PROCESSING = "model_processing"
    WEB_INTERACTION = "web_interaction"


class StrictRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ResourceLimits(StrictRecord):
    """Operator-approved limits; no CPU/RAM threshold is guessed by CONTX."""

    p95_total_cpu_percent_max: float | None = Field(default=None, gt=0, le=1600)
    p95_total_rss_bytes_max: int | None = Field(default=None, gt=0)
    p95_detection_latency_ms_max: float | None = Field(default=None, gt=0)
    raw_disk_bytes_max: int = Field(default=5 * 1024**3, gt=0)

    @property
    def fully_defined(self) -> bool:
        return all(
            value is not None
            for value in (
                self.p95_total_cpu_percent_max,
                self.p95_total_rss_bytes_max,
                self.p95_detection_latency_ms_max,
            )
        )


class PilotThresholds(StrictRecord):
    materially_false_memory_rate_max: float = Field(default=0.10, ge=0, le=1)
    important_event_recall_min_exclusive: float = Field(default=0.50, ge=0, lt=1)
    provenance_coverage_min: float = Field(default=1.0, ge=0, le=1)
    minimum_behavior_quality_delta: float = Field(default=0.05, gt=0, le=1)
    max_false_claim_rate_regression: float = Field(default=0.0, ge=0, le=1)
    wake_budget_bytes: int = Field(default=20_000, ge=4096, le=32768)
    raw_retention_hours_max: int = Field(default=48, ge=1, le=48)
    resource_limits: ResourceLimits = Field(default_factory=ResourceLimits)


class PilotManifest(StrictRecord):
    schema_version: Literal[1] = 1
    pilot_id: UUID
    started_at: datetime
    planned_end_at: datetime
    timezone: str = Field(min_length=1, max_length=64)
    target_machine: str = Field(min_length=1, max_length=255)
    thresholds: PilotThresholds = Field(default_factory=PilotThresholds)

    _utc_timestamps = field_validator("started_at", "planned_end_at")(
        require_aware_utc
    )

    @field_validator("timezone", "target_machine")
    @classmethod
    def validate_single_line(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or any(character in normalized for character in "\r\n"):
            raise ValueError("value must be one non-empty line")
        return normalized

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as error:
            raise ValueError("timezone must be a valid IANA timezone") from error
        return value

    @model_validator(mode="after")
    def validate_duration(self) -> Self:
        duration = self.planned_end_at - self.started_at
        if duration < timedelta(days=7) or duration > timedelta(days=14):
            raise ValueError("pilot duration must be between 7 and 14 days")
        return self


class GroundTruthEntry(StrictRecord):
    schema_version: Literal[1] = 1
    id: UUID
    occurred_at: datetime
    ended_at: datetime | None = None
    kind: GroundTruthKind
    importance: GroundTruthImportance
    scenarios: tuple[PilotScenario, ...] = Field(min_length=1)
    summary: str = Field(min_length=1, max_length=2000)
    project: str | None = Field(default=None, max_length=255)

    _utc_timestamps = field_validator("occurred_at", "ended_at")(
        lambda value: None if value is None else require_aware_utc(value)
    )

    @field_validator("summary")
    @classmethod
    def validate_summary(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("summary must not be blank")
        return normalized

    @field_validator("project")
    @classmethod
    def validate_project(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("project must not be blank")
        return normalized

    @model_validator(mode="after")
    def validate_interval(self) -> Self:
        if self.ended_at is not None and self.ended_at < self.occurred_at:
            raise ValueError("ended_at must not be before occurred_at")
        if len(set(self.scenarios)) != len(self.scenarios):
            raise ValueError("scenarios must not contain duplicates")
        return self


class TrialEvaluation(StrictRecord):
    """Content-minimized human scoring for one agent answer."""

    schema_version: Literal[1] = 1
    id: UUID
    pair_key: str = Field(min_length=1, max_length=128)
    prompt_key: str = Field(min_length=1, max_length=128)
    scenario: PilotScenario
    condition: PilotCondition
    evaluated_at: datetime
    relevant_truth_ids: tuple[UUID, ...] = Field(min_length=1)
    retrieved_truth_ids: tuple[UUID, ...] = ()
    supported_factual_claims: int = Field(ge=0)
    total_factual_claims: int = Field(ge=0)
    materially_false_claims: int = Field(ge=0)
    irrelevant_claims: int = Field(ge=0)
    duplicate_claims: int = Field(ge=0)
    clarification_questions: int = Field(ge=0)
    relevance_score: int = Field(ge=1, le=5)
    synthesis_score: int = Field(ge=1, le=5)
    response_latency_ms: float = Field(ge=0)
    context_bytes: int = Field(default=0, ge=0)
    wake_latency_ms: float | None = Field(default=None, ge=0)

    _utc_timestamp = field_validator("evaluated_at")(require_aware_utc)

    @field_validator("pair_key", "prompt_key")
    @classmethod
    def validate_key(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or any(character in normalized for character in "\r\n"):
            raise ValueError("key must be one non-empty line")
        return normalized

    @model_validator(mode="after")
    def validate_counts_and_condition(self) -> Self:
        if len(set(self.relevant_truth_ids)) != len(self.relevant_truth_ids):
            raise ValueError("relevant_truth_ids must not contain duplicates")
        if len(set(self.retrieved_truth_ids)) != len(self.retrieved_truth_ids):
            raise ValueError("retrieved_truth_ids must not contain duplicates")
        if not set(self.retrieved_truth_ids).issubset(self.relevant_truth_ids):
            raise ValueError("retrieved_truth_ids must be relevant to the trial")
        if self.supported_factual_claims > self.total_factual_claims:
            raise ValueError("supported claims cannot exceed total factual claims")
        if self.materially_false_claims > self.total_factual_claims:
            raise ValueError("false claims cannot exceed total factual claims")
        if (
            self.supported_factual_claims + self.materially_false_claims
            > self.total_factual_claims
        ):
            raise ValueError("supported and false claims cannot exceed total claims")
        if self.condition is PilotCondition.WITHOUT_CONTX:
            if self.context_bytes != 0 or self.wake_latency_ms is not None:
                raise ValueError(
                    "without_contx trials cannot record CONTX context or wake latency"
                )
        elif self.wake_latency_ms is None:
            raise ValueError("with_contx trials require a measured wake latency")
        return self


class TechnicalSnapshot(StrictRecord):
    schema_version: Literal[1] = 1
    captured_at: datetime
    accepted_memories: int = Field(ge=0)
    accepted_memories_with_provenance: int = Field(ge=0)
    materially_false_memories: int = Field(ge=0)
    irrelevant_memories: int = Field(ge=0)
    duplicate_memories: int = Field(ge=0)
    manual_corrections: int = Field(ge=0)
    sensitive_promotions: int = Field(ge=0)
    synthetic_secret_promotions: int = Field(ge=0)
    model_outputs: int = Field(ge=0)
    invalid_model_outputs: int = Field(ge=0)
    unknown_model_attempts: int = Field(ge=0)
    raw_records_past_retention: int = Field(ge=0)
    excluded_context_captures: int = Field(ge=0)
    remote_user_content_transports: int = Field(ge=0)
    active_context_bytes: int = Field(ge=0)
    wake_latency_ms: float = Field(ge=0)

    _utc_timestamp = field_validator("captured_at")(require_aware_utc)

    @model_validator(mode="after")
    def validate_counts(self) -> Self:
        if self.accepted_memories_with_provenance > self.accepted_memories:
            raise ValueError("provenanced memories cannot exceed accepted memories")
        if self.materially_false_memories > self.accepted_memories:
            raise ValueError("false memories cannot exceed accepted memories")
        if self.invalid_model_outputs > self.model_outputs:
            raise ValueError("invalid model outputs cannot exceed all model outputs")
        return self


class ResourceSample(StrictRecord):
    schema_version: Literal[1] = 1
    captured_at: datetime
    phase: ResourcePhase
    collector_cpu_percent: float = Field(ge=0, le=1600)
    collector_rss_bytes: int = Field(gt=0)
    local_model_cpu_percent: float = Field(default=0, ge=0, le=1600)
    local_model_rss_bytes: int = Field(default=0, ge=0)
    web_cpu_percent: float = Field(default=0, ge=0, le=1600)
    web_rss_bytes: int = Field(default=0, ge=0)
    detection_latency_ms: float | None = Field(default=None, ge=0)
    raw_disk_bytes: int = Field(ge=0)
    durable_disk_bytes: int = Field(ge=0)

    _utc_timestamp = field_validator("captured_at")(require_aware_utc)

    @property
    def total_cpu_percent(self) -> float:
        return (
            self.collector_cpu_percent
            + self.local_model_cpu_percent
            + self.web_cpu_percent
        )

    @property
    def total_rss_bytes(self) -> int:
        return (
            self.collector_rss_bytes
            + self.local_model_rss_bytes
            + self.web_rss_bytes
        )

    @model_validator(mode="after")
    def validate_phase_evidence(self) -> Self:
        if (
            self.phase is ResourcePhase.ACTIVE_COLLECTION
            and self.detection_latency_ms is None
        ):
            raise ValueError(
                "active collection samples require measured detection latency"
            )
        if (
            self.phase is ResourcePhase.MODEL_PROCESSING
            and self.local_model_rss_bytes == 0
        ):
            raise ValueError("model-processing samples require local-model RSS")
        if self.phase is ResourcePhase.WEB_INTERACTION and self.web_rss_bytes == 0:
            raise ValueError("web-interaction samples require web RSS")
        return self


class PrivacyIncident(StrictRecord):
    schema_version: Literal[1] = 1
    id: UUID
    occurred_at: datetime
    category: PrivacyIncidentCategory
    severity: Severity
    summary: str = Field(min_length=1, max_length=1000)
    resolved_at: datetime | None = None

    _utc_timestamps = field_validator("occurred_at", "resolved_at")(
        lambda value: None if value is None else require_aware_utc(value)
    )

    @field_validator("summary")
    @classmethod
    def validate_summary(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("summary must not be blank")
        return normalized

    @model_validator(mode="after")
    def validate_resolution(self) -> Self:
        if self.resolved_at is not None and self.resolved_at < self.occurred_at:
            raise ValueError("resolved_at must not be before occurred_at")
        return self


class PilotDataset(StrictRecord):
    manifest: PilotManifest
    ground_truth: tuple[GroundTruthEntry, ...]
    trials: tuple[TrialEvaluation, ...]
    technical_snapshots: tuple[TechnicalSnapshot, ...]
    resource_samples: tuple[ResourceSample, ...]
    privacy_incidents: tuple[PrivacyIncident, ...]
