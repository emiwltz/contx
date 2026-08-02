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


class Sensitivity(StrEnum):
    PUBLIC = "public"
    PERSONAL = "personal"
    SENSITIVE = "sensitive"
    FORBIDDEN = "forbidden"


class CandidateStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    DEFERRED = "deferred"
    STORED = "stored"


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
    type: str = Field(min_length=1, max_length=64)
    summary: str = Field(min_length=1, max_length=2000)
    facts: dict[str, Any]
    started_at: datetime
    ended_at: datetime
    epistemic_status: EpistemicStatus
    confidence: float = Field(ge=0.0, le=1.0)
    sensitivity: Sensitivity
    projects: tuple[str, ...] = ()
    entities: tuple[str, ...] = ()
    source_observation_ids: tuple[UUID, ...] = Field(min_length=1)
    processing_version: str = Field(min_length=1, max_length=64)
    created_at: datetime
    updated_at: datetime

    _utc_timestamps = field_validator(
        "started_at", "ended_at", "created_at", "updated_at"
    )(require_aware_utc)

    @model_validator(mode="after")
    def validate_event(self) -> Self:
        if self.started_at > self.ended_at:
            raise ValueError("started_at must not be after ended_at")
        if len(set(self.source_observation_ids)) != len(self.source_observation_ids):
            raise ValueError("source observation identifiers must be unique")
        return self


class MemoryCandidate(DomainRecord):
    id: UUID
    idempotency_key: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]+$")
    text: str = Field(min_length=1, max_length=4000)
    source_type: str = Field(min_length=1, max_length=64)
    source_ids: tuple[UUID, ...] = Field(min_length=1)
    importance: float = Field(ge=0.0, le=1.0)
    durability: float = Field(ge=0.0, le=1.0)
    novelty: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    sensitivity: Sensitivity
    score: float = Field(ge=0.0, le=1.0)
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
