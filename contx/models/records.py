"""Pydantic domain records for v0.0.1."""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from contx.models.common import require_aware_utc


class SourceType(StrEnum):
    SYNTHETIC = "synthetic"
    ACTIVE_APP = "active_app"
    SYSTEM_STATE = "system_state"
    EXCLUDED_ACTIVITY = "excluded_activity"
    SCREENSHOT = "screenshot"


class ActivityState(StrEnum):
    ACTIVE = "active"
    IDLE = "idle"
    LOCKED = "locked"
    ASLEEP = "asleep"


class ObservationStatus(StrEnum):
    COLLECTED = "collected"
    PROCESSED = "processed"
    REJECTED = "rejected"
    PURGED = "purged"


class ExclusionRuleType(StrEnum):
    APP_BUNDLE_ID = "app_bundle_id"
    APP_NAME_CONTAINS = "app_name_contains"
    WINDOW_TITLE_CONTAINS = "window_title_contains"
    SITUATION = "situation"


class ExclusionScope(StrEnum):
    ALL = "all"


class EpistemicStatus(StrEnum):
    OBSERVED = "observed"
    INFERRED = "inferred"
    HYPOTHETICAL = "hypothetical"


class EventType(StrEnum):
    """Bounded event vocabulary used by replay, timelines, and patterns."""

    PROJECT_WORK = "project_work"
    CODING = "coding"
    TESTING = "testing"
    DOCUMENT_EDITING = "document_editing"
    RESEARCH = "research"
    COMMUNICATION = "communication"
    PLANNING = "planning"
    SYSTEM_ADMINISTRATION = "system_administration"
    MIXED_ACTIVITY = "mixed_activity"
    PROJECT_RESUMPTION = "project_resumption"
    BRIEF_ACTIVITY = "brief_activity"
    OTHER = "other"


class Sensitivity(StrEnum):
    PUBLIC = "public"
    PERSONAL = "personal"
    SENSITIVE = "sensitive"
    FORBIDDEN = "forbidden"

    @property
    def permits_durable_memory(self) -> bool:
        """Return whether this classification may cross the memory boundary."""
        return self in {Sensitivity.PUBLIC, Sensitivity.PERSONAL}


class CandidateStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    DEFERRED = "deferred"
    STORED = "stored"


class CandidateDecisionStatus(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    DEFERRED = "deferred"


class PatternType(StrEnum):
    PROJECT_RECURRENCE = "project_recurrence"
    PROJECT_RESUMPTION = "project_resumption"
    ACTIVITY_INCREASE = "activity_increase"
    ACTIVITY_DECREASE = "activity_decrease"
    NEW_REPEATED_ACTIVITY = "new_repeated_activity"


class PatternStatus(StrEnum):
    ACTIVE = "active"
    EXPIRED = "expired"
    SUPERSEDED = "superseded"


class MemoryLinkStatus(StrEnum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    FORGOTTEN = "forgotten"


class ProcessingRunStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class DomainRecord(BaseModel):
    """Strict immutable base for observable product contracts."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class Observation(DomainRecord):
    id: UUID
    idempotency_key: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]+$")
    source_type: SourceType
    activity_state: ActivityState = ActivityState.ACTIVE
    captured_at: datetime
    started_at: datetime | None = None
    ended_at: datetime | None = None
    app_name: str | None = Field(default=None, max_length=255)
    app_bundle_id: str | None = Field(default=None, max_length=255)
    window_title: str | None = None
    artifact_path: str | None = None
    content_hash: str | None = Field(
        default=None,
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]+$",
    )
    perceptual_hash: str | None = Field(default=None, max_length=128)
    excluded: bool = False
    exclusion_reason: str | None = Field(default=None, max_length=255)
    processing_status: ObservationStatus = ObservationStatus.COLLECTED
    expires_at: datetime
    created_at: datetime

    _utc_timestamps = field_validator(
        "captured_at", "started_at", "ended_at", "expires_at", "created_at"
    )(require_aware_utc)

    @model_validator(mode="after")
    def validate_observation(self) -> Self:
        if self.started_at and self.ended_at and self.started_at > self.ended_at:
            raise ValueError("started_at must not be after ended_at")
        if self.excluded != (self.exclusion_reason is not None):
            raise ValueError(
                "excluded observations require exactly one exclusion reason"
            )
        if self.expires_at <= self.captured_at:
            raise ValueError("expires_at must be after captured_at")
        if self.expires_at - self.captured_at > timedelta(hours=48):
            raise ValueError("raw observation retention must not exceed 48 hours")
        if self.processing_status is ObservationStatus.PURGED and any(
            value is not None
            for value in (
                self.app_name,
                self.app_bundle_id,
                self.window_title,
                self.artifact_path,
                self.content_hash,
                self.perceptual_hash,
            )
        ):
            raise ValueError("purged observations must not retain raw content")
        return self


class ExclusionRule(DomainRecord):
    id: UUID
    rule_type: ExclusionRuleType
    pattern: str = Field(min_length=1, max_length=255)
    scope: ExclusionScope = ExclusionScope.ALL
    enabled: bool = True
    built_in: bool = False
    created_at: datetime
    updated_at: datetime

    _utc_timestamps = field_validator("created_at", "updated_at")(require_aware_utc)

    @field_validator("pattern")
    @classmethod
    def validate_pattern(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or any(character in normalized for character in "\r\n"):
            raise ValueError("exclusion patterns must be one non-empty line")
        return normalized


class CollectionControl(DomainRecord):
    paused_at: datetime | None = None
    pause_until: datetime | None = None
    updated_at: datetime

    _utc_timestamps = field_validator("paused_at", "pause_until", "updated_at")(
        lambda value: None if value is None else require_aware_utc(value)
    )

    @model_validator(mode="after")
    def validate_pause(self) -> Self:
        if self.pause_until is not None and self.paused_at is None:
            raise ValueError("pause_until requires paused_at")
        if (
            self.paused_at is not None
            and self.pause_until is not None
            and self.pause_until <= self.paused_at
        ):
            raise ValueError("pause_until must be after paused_at")
        return self

    def is_paused(self, *, at: datetime) -> bool:
        current = require_aware_utc(at)
        return self.paused_at is not None and (
            self.pause_until is None or current < self.pause_until
        )

    def pause(self, *, at: datetime, until: datetime | None = None) -> Self:
        paused_at = require_aware_utc(at)
        return type(self)(
            paused_at=paused_at,
            pause_until=None if until is None else require_aware_utc(until),
            updated_at=paused_at,
        )

    def resume(self, *, at: datetime) -> Self:
        resumed_at = require_aware_utc(at)
        return type(self)(updated_at=resumed_at)


class Event(DomainRecord):
    id: UUID
    idempotency_key: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]+$")
    lineage_key: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]+$")
    type: EventType
    summary: str = Field(min_length=1, max_length=2000, repr=False)
    facts: dict[str, Any] = Field(repr=False)
    started_at: datetime
    ended_at: datetime
    valid_from: datetime
    valid_until: datetime | None = None
    epistemic_status: EpistemicStatus
    confidence: float = Field(ge=0.0, le=1.0)
    sensitivity: Sensitivity
    projects: tuple[str, ...] = Field(default=(), repr=False)
    entities: tuple[str, ...] = Field(default=(), repr=False)
    source_observation_ids: tuple[UUID, ...] = Field(min_length=1)
    processing_version: str = Field(min_length=1, max_length=64)
    created_at: datetime
    updated_at: datetime

    _utc_timestamps = field_validator(
        "started_at",
        "ended_at",
        "valid_from",
        "valid_until",
        "created_at",
        "updated_at",
    )(lambda value: None if value is None else require_aware_utc(value))

    @model_validator(mode="after")
    def validate_event(self) -> Self:
        if self.started_at > self.ended_at:
            raise ValueError("started_at must not be after ended_at")
        if self.valid_until is not None and self.valid_from > self.valid_until:
            raise ValueError("valid_from must not be after valid_until")
        if len(set(self.source_observation_ids)) != len(self.source_observation_ids):
            raise ValueError("source observation identifiers must be unique")
        if len(set(self.projects)) != len(self.projects):
            raise ValueError("event projects must be unique")
        if len(set(self.entities)) != len(self.entities):
            raise ValueError("event entities must be unique")
        return self


class EventCorrectionContent(DomainRecord):
    """Complete corrected semantic view without replacing source evidence."""

    type: EventType
    summary: str = Field(min_length=1, max_length=2000, repr=False)
    epistemic_status: EpistemicStatus
    confidence: float = Field(ge=0.0, le=1.0)
    projects: tuple[str, ...] = Field(default=(), repr=False)
    entities: tuple[str, ...] = Field(default=(), repr=False)
    valid_from: datetime
    valid_until: datetime | None = None

    _utc_timestamps = field_validator("valid_from", "valid_until")(
        lambda value: None if value is None else require_aware_utc(value)
    )

    @model_validator(mode="after")
    def validate_content(self) -> Self:
        if self.valid_until is not None and self.valid_from > self.valid_until:
            raise ValueError("valid_from must not be after valid_until")
        if len(set(self.projects)) != len(self.projects):
            raise ValueError("corrected projects must be unique")
        if len(set(self.entities)) != len(self.entities):
            raise ValueError("corrected entities must be unique")
        return self


class EventCorrection(DomainRecord):
    """Append-only user correction attached to one stable event lineage."""

    id: UUID
    idempotency_key: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]+$")
    event_lineage_key: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]+$",
    )
    target_event_id: UUID
    replacement: EventCorrectionContent = Field(repr=False)
    reason: str = Field(min_length=1, max_length=500, repr=False)
    supersedes_correction_id: UUID | None = None
    created_at: datetime

    _utc_timestamp = field_validator("created_at")(require_aware_utc)

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("correction reason must not be empty")
        return normalized


class TimelineBuild(DomainRecord):
    """Content-free parameters for one reproducible activity-timeline replay."""

    processing_run_id: UUID
    processing_version: str = Field(min_length=1, max_length=64)
    window_start: datetime
    window_end: datetime
    session_gap_seconds: int = Field(ge=30, le=3600)
    max_session_duration_seconds: int = Field(ge=300, le=14400)

    _utc_timestamps = field_validator("window_start", "window_end")(require_aware_utc)

    @model_validator(mode="after")
    def validate_build(self) -> Self:
        if self.window_start >= self.window_end:
            raise ValueError("timeline window must have a positive duration")
        if self.max_session_duration_seconds <= self.session_gap_seconds:
            raise ValueError("timeline session maximum must exceed its gap")
        return self


class TimelineEntry(DomainRecord):
    """Effective event semantics for one selected replay and correction chain."""

    event_id: UUID
    correction_id: UUID | None = None
    lineage_key: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]+$")
    type: EventType
    summary: str = Field(min_length=1, max_length=2000, repr=False)
    started_at: datetime
    ended_at: datetime
    valid_from: datetime
    valid_until: datetime | None = None
    epistemic_status: EpistemicStatus
    confidence: float = Field(ge=0.0, le=1.0)
    sensitivity: Sensitivity
    projects: tuple[str, ...] = Field(default=(), repr=False)
    entities: tuple[str, ...] = Field(default=(), repr=False)
    source_observation_ids: tuple[UUID, ...] = Field(min_length=1)
    processing_version: str = Field(min_length=1, max_length=64)

    _utc_timestamps = field_validator(
        "started_at",
        "ended_at",
        "valid_from",
        "valid_until",
    )(lambda value: None if value is None else require_aware_utc(value))

    @model_validator(mode="after")
    def validate_entry(self) -> Self:
        if self.started_at > self.ended_at:
            raise ValueError("timeline entry cannot end before it starts")
        if self.valid_until is not None and self.valid_from > self.valid_until:
            raise ValueError("timeline entry validity cannot run backwards")
        if len(set(self.source_observation_ids)) != len(self.source_observation_ids):
            raise ValueError("timeline observation identifiers must be unique")
        return self


class ActivityTimeline(DomainRecord):
    """One immutable effective view of a successful timeline processing run."""

    build: TimelineBuild
    entries: tuple[TimelineEntry, ...]

    @model_validator(mode="after")
    def validate_timeline(self) -> Self:
        if (
            tuple(
                sorted(
                    self.entries,
                    key=lambda item: (
                        item.started_at,
                        item.ended_at,
                        str(item.event_id),
                    ),
                )
            )
            != self.entries
        ):
            raise ValueError("timeline entries must be ordered")
        if any(
            entry.processing_version != self.build.processing_version
            for entry in self.entries
        ):
            raise ValueError("timeline entries must match the replay version")
        return self


class Pattern(DomainRecord):
    """A multi-event, replayable temporal inference."""

    id: UUID
    idempotency_key: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]+$")
    type: PatternType
    summary: str = Field(min_length=1, max_length=2000, repr=False)
    window_start: datetime
    window_end: datetime
    epistemic_status: EpistemicStatus
    confidence: float = Field(ge=0.0, le=1.0)
    sensitivity: Sensitivity
    evidence_count: int = Field(ge=2)
    source_event_ids: tuple[UUID, ...] = Field(min_length=2)
    projects: tuple[str, ...] = Field(default=(), repr=False)
    entities: tuple[str, ...] = Field(default=(), repr=False)
    metrics: dict[str, float | int | str] = Field(default_factory=dict)
    valid_from: datetime
    valid_until: datetime | None = None
    status: PatternStatus = PatternStatus.ACTIVE
    processing_version: str = Field(min_length=1, max_length=64)
    created_at: datetime

    _utc_timestamps = field_validator(
        "window_start",
        "window_end",
        "valid_from",
        "valid_until",
        "created_at",
    )(lambda value: None if value is None else require_aware_utc(value))

    @model_validator(mode="after")
    def validate_pattern(self) -> Self:
        if self.window_start > self.window_end:
            raise ValueError("pattern window cannot run backwards")
        if self.valid_from < self.window_end:
            raise ValueError(
                "pattern cannot become valid before its evidence window ends"
            )
        if self.valid_until is not None and self.valid_from > self.valid_until:
            raise ValueError("pattern validity cannot run backwards")
        if self.evidence_count != len(self.source_event_ids):
            raise ValueError("pattern evidence count must match source events")
        if len(set(self.source_event_ids)) != len(self.source_event_ids):
            raise ValueError("pattern source event identifiers must be unique")
        if len(set(self.projects)) != len(self.projects):
            raise ValueError("pattern projects must be unique")
        if len(set(self.entities)) != len(self.entities):
            raise ValueError("pattern entities must be unique")
        return self

    def status_at(self, at: datetime) -> PatternStatus:
        """Resolve time-based expiration without mutating replay evidence."""
        instant = require_aware_utc(at)
        if (
            self.status is PatternStatus.ACTIVE
            and self.valid_until is not None
            and instant >= self.valid_until
        ):
            return PatternStatus.EXPIRED
        return self.status


class PatternBuild(DomainRecord):
    """Content-free inputs for one reproducible multi-event pattern replay."""

    processing_run_id: UUID
    source_timeline_run_id: UUID
    processing_version: str = Field(min_length=1, max_length=64)
    comparison_boundary: datetime
    min_project_events: int = Field(ge=2, le=100)
    resumption_gap_seconds: int = Field(ge=3600, le=30 * 24 * 3600)
    change_ratio: float = Field(ge=1.1, le=10.0)

    _utc_timestamp = field_validator("comparison_boundary")(require_aware_utc)


class CandidateBuild(DomainRecord):
    """Content-free provenance for one pattern-to-candidate replay."""

    processing_run_id: UUID
    source_pattern_run_id: UUID
    processing_version: str = Field(min_length=1, max_length=64)
    scoring_weights: dict[str, float]

    @field_validator("scoring_weights")
    @classmethod
    def validate_scoring_weights(cls, value: dict[str, float]) -> dict[str, float]:
        if not value:
            raise ValueError("candidate scoring weights cannot be empty")
        if any(
            not key.strip() or not -1.0 <= weight <= 1.0
            for key, weight in value.items()
        ):
            raise ValueError("candidate scoring weights are invalid")
        return value


class MemoryCandidate(DomainRecord):
    id: UUID
    idempotency_key: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]+$")
    text: str = Field(min_length=1, max_length=4000, repr=False)
    source_type: str = Field(min_length=1, max_length=64)
    source_ids: tuple[UUID, ...] = Field(min_length=1)
    utility: float = Field(default=0.0, ge=0.0, le=1.0)
    importance: float = Field(ge=0.0, le=1.0)
    durability: float = Field(ge=0.0, le=1.0)
    novelty: float = Field(ge=0.0, le=1.0)
    recurrence: float = Field(default=0.0, ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    ambiguity: float = Field(default=0.0, ge=0.0, le=1.0)
    redundancy: float = Field(default=0.0, ge=0.0, le=1.0)
    sensitivity: Sensitivity
    score: float = Field(ge=0.0, le=1.0)
    scoring_version: str = Field(
        default="legacy-candidate-v1", min_length=1, max_length=64
    )
    status: CandidateStatus = CandidateStatus.PENDING
    rejection_reason: str | None = Field(default=None, max_length=255)
    created_at: datetime
    processed_at: datetime | None = None

    _utc_timestamps = field_validator("created_at", "processed_at")(
        lambda value: None if value is None else require_aware_utc(value)
    )

    @model_validator(mode="after")
    def validate_candidate(self) -> Self:
        if len(set(self.source_ids)) != len(self.source_ids):
            raise ValueError("candidate source identifiers must be unique")
        terminal = self.status is not CandidateStatus.PENDING
        if terminal != (self.processed_at is not None):
            raise ValueError("processed_at is required exactly for decided candidates")
        needs_reason = self.status in {
            CandidateStatus.REJECTED,
            CandidateStatus.DEFERRED,
        }
        if needs_reason != (self.rejection_reason is not None):
            raise ValueError(
                "rejected or deferred candidates require exactly one reason"
            )
        return self

    def decide(
        self,
        status: CandidateStatus,
        *,
        processed_at: datetime,
        reason: str | None = None,
    ) -> Self:
        """Apply one irreversible worker decision."""
        if self.status is not CandidateStatus.PENDING:
            raise ValueError("only pending candidates can be decided")
        if status not in {
            CandidateStatus.ACCEPTED,
            CandidateStatus.REJECTED,
            CandidateStatus.DEFERRED,
        }:
            raise ValueError("invalid candidate decision status")
        return type(self).model_validate(
            self.model_dump()
            | {
                "status": status,
                "processed_at": processed_at,
                "rejection_reason": reason,
            }
        )

    def mark_stored(self, *, processed_at: datetime) -> Self:
        """Record successful persistence after an accepted decision."""
        if self.status is not CandidateStatus.ACCEPTED:
            raise ValueError("only accepted candidates can be marked stored")
        return type(self).model_validate(
            self.model_dump()
            | {"status": CandidateStatus.STORED, "processed_at": processed_at}
        )


class CandidateDecision(DomainRecord):
    """One append-only evaluation of a candidate under an explicit policy."""

    id: UUID
    idempotency_key: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]+$")
    candidate_id: UUID
    processing_run_id: UUID
    policy_version: str = Field(min_length=1, max_length=64)
    acceptance_threshold: float = Field(ge=0.0, le=1.0)
    status: CandidateDecisionStatus
    reason: str | None = Field(default=None, max_length=255)
    created_at: datetime

    _utc_timestamp = field_validator("created_at")(require_aware_utc)

    @model_validator(mode="after")
    def validate_decision(self) -> Self:
        needs_reason = self.status in {
            CandidateDecisionStatus.REJECTED,
            CandidateDecisionStatus.DEFERRED,
        }
        if needs_reason != (self.reason is not None):
            raise ValueError("rejected or deferred evaluations require one reason")
        return self


class CandidateEvaluationBuild(DomainRecord):
    """Content-free policy inputs for one candidate evaluation replay."""

    processing_run_id: UUID
    source_candidate_run_id: UUID
    policy_version: str = Field(min_length=1, max_length=64)
    acceptance_threshold: float = Field(ge=0.0, le=1.0)
    minimum_confidence: float = Field(ge=0.0, le=1.0)
    maximum_ambiguity: float = Field(ge=0.0, le=1.0)
    maximum_redundancy: float = Field(ge=0.0, le=1.0)


class MemoryProvenance(DomainRecord):
    candidate_id: UUID
    event_ids: tuple[UUID, ...] = Field(min_length=1)
    observation_ids: tuple[UUID, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_ids(self) -> Self:
        if len(set(self.event_ids)) != len(self.event_ids):
            raise ValueError("provenance event identifiers must be unique")
        if len(set(self.observation_ids)) != len(self.observation_ids):
            raise ValueError("provenance observation identifiers must be unique")
        return self


class MemoryLink(DomainRecord):
    id: UUID
    memory_backend_id: str = Field(min_length=1, max_length=255)
    candidate_id: UUID
    provenance: MemoryProvenance
    confidence: float = Field(ge=0.0, le=1.0)
    status: MemoryLinkStatus = MemoryLinkStatus.ACTIVE
    supersedes_memory_id: UUID | None = None
    created_at: datetime

    _utc_timestamp = field_validator("created_at")(require_aware_utc)

    @model_validator(mode="after")
    def validate_candidate_identity(self) -> Self:
        if self.candidate_id != self.provenance.candidate_id:
            raise ValueError("memory provenance must reference the linked candidate")
        return self


class ProcessingRun(DomainRecord):
    id: UUID
    pipeline: str = Field(min_length=1, max_length=64)
    version: str = Field(min_length=1, max_length=64)
    started_at: datetime
    ended_at: datetime | None = None
    status: ProcessingRunStatus = ProcessingRunStatus.RUNNING
    input_count: int = Field(default=0, ge=0)
    output_count: int = Field(default=0, ge=0)
    error_code: str | None = Field(default=None, max_length=64)
    error_summary: str | None = Field(default=None, max_length=255)

    _utc_timestamps = field_validator("started_at", "ended_at")(
        lambda value: None if value is None else require_aware_utc(value)
    )

    @model_validator(mode="after")
    def validate_run(self) -> Self:
        finished = self.status is not ProcessingRunStatus.RUNNING
        if finished != (self.ended_at is not None):
            raise ValueError("ended_at is required exactly for finished runs")
        failed = self.status is ProcessingRunStatus.FAILED
        if failed != (self.error_code is not None):
            raise ValueError("failed runs require exactly one error code")
        if self.error_summary and any(
            character in self.error_summary for character in "\r\n"
        ):
            raise ValueError("error summaries must be single-line and sanitized")
        return self

    def succeed(
        self,
        *,
        ended_at: datetime,
        input_count: int,
        output_count: int,
    ) -> Self:
        """Finish a running pipeline execution successfully."""
        if self.status is not ProcessingRunStatus.RUNNING:
            raise ValueError("only running processing runs can finish")
        return type(self).model_validate(
            self.model_dump()
            | {
                "ended_at": ended_at,
                "status": ProcessingRunStatus.SUCCEEDED,
                "input_count": input_count,
                "output_count": output_count,
            }
        )

    def fail(
        self,
        *,
        ended_at: datetime,
        error_code: str,
        error_summary: str | None = None,
        input_count: int = 0,
        output_count: int = 0,
    ) -> Self:
        """Finish a run with a bounded, sanitized operational error."""
        if self.status is not ProcessingRunStatus.RUNNING:
            raise ValueError("only running processing runs can finish")
        return type(self).model_validate(
            self.model_dump()
            | {
                "ended_at": ended_at,
                "status": ProcessingRunStatus.FAILED,
                "input_count": input_count,
                "output_count": output_count,
                "error_code": error_code,
                "error_summary": error_summary,
            }
        )
