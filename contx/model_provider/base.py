"""Typed contracts for mandatory local multimodal interpretation."""

from __future__ import annotations

import hashlib
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, Protocol, Self
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationInfo,
    field_validator,
    model_validator,
)

from contx.model_provider.endpoint import LoopbackHttpEndpoint
from contx.models import ActivityState, Sensitivity
from contx.models.common import require_aware_utc

type PromptVersion = Literal[
    "local-screen-v1",
    "local-screen-v2",
    "local-screen-v3",
    "local-screen-v4",
    "local-screen-v5",
    "local-screen-v6",
    "local-screen-v7",
    "local-screen-v8",
    "local-screen-v9",
]
PROMPT_VERSION: PromptVersion = "local-screen-v9"
OUTPUT_SCHEMA_VERSION = "model-interpretation-v1"

PrivateSummary = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=2000),
]
PrivateStatement = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=500),
]
PrivateLabel = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=120,
        pattern=r"^[^\r\n]+$",
    ),
]


class SensitiveCategory(StrEnum):
    """Sensitive content categories the local model can flag."""

    AUTHENTICATION = "authentication"
    CREDENTIAL = "credential"
    FINANCIAL = "financial"
    HEALTH = "health"
    GOVERNMENT_IDENTIFIER = "government_identifier"
    THIRD_PARTY_PRIVATE = "third_party_private"
    USER_FORBIDDEN = "user_forbidden"
    OTHER = "other"


class LocalModelRequest(BaseModel):
    """One authorized local image plus the metadata needed to interpret it."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    id: UUID
    source_observation_ids: tuple[UUID, ...] = Field(min_length=1, max_length=32)
    captured_at: datetime
    started_at: datetime | None = None
    ended_at: datetime | None = None
    activity_state: ActivityState
    app_name: str | None = Field(
        default=None,
        max_length=255,
        exclude=True,
        repr=False,
    )
    app_bundle_id: str | None = Field(
        default=None,
        max_length=255,
        exclude=True,
        repr=False,
    )
    window_title: str | None = Field(
        default=None,
        max_length=4000,
        exclude=True,
        repr=False,
    )
    image_media_type: Literal["image/png"] = "image/png"
    image_sha256: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]+$")
    image_bytes: bytes = Field(min_length=1, exclude=True, repr=False)

    _utc_timestamps = field_validator("captured_at", "started_at", "ended_at")(
        lambda value: None if value is None else require_aware_utc(value)
    )

    @model_validator(mode="after")
    def validate_request(self) -> Self:
        if len(set(self.source_observation_ids)) != len(self.source_observation_ids):
            raise ValueError("model source observation identifiers must be unique")
        if self.started_at and self.ended_at and self.started_at > self.ended_at:
            raise ValueError("started_at must not be after ended_at")
        if not self.image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("local model image must be a PNG")
        if hashlib.sha256(self.image_bytes).hexdigest() != self.image_sha256:
            raise ValueError("local model image hash does not match its content")
        return self

    def prompt_metadata(self) -> dict[str, object]:
        """Return private metadata only for the explicit local-model call."""
        return {
            "captured_at": self.captured_at.isoformat(),
            "started_at": (
                None if self.started_at is None else self.started_at.isoformat()
            ),
            "ended_at": None if self.ended_at is None else self.ended_at.isoformat(),
            "activity_state": self.activity_state.value,
            "application_name": self.app_name,
            "application_bundle_id": self.app_bundle_id,
            "window_title": self.window_title,
        }


class ModelInterpretation(BaseModel):
    """Strict semantic result accepted from the local multimodal model."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    summary: PrivateSummary = Field(repr=False)
    activity_type: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True,
            min_length=1,
            max_length=64,
            pattern=r"^[a-z][a-z0-9_]*$",
        ),
    ]
    observed_facts: tuple[PrivateStatement, ...] = Field(
        min_length=1,
        max_length=20,
        repr=False,
    )
    inferred_context: tuple[PrivateStatement, ...] = Field(
        max_length=20,
        repr=False,
    )
    projects: tuple[PrivateLabel, ...] = Field(max_length=10, repr=False)
    entities: tuple[PrivateLabel, ...] = Field(max_length=20, repr=False)
    sensitivity: Sensitivity
    sensitive_categories: tuple[SensitiveCategory, ...] = Field(max_length=8)
    confidence: float = Field(ge=0.0, le=1.0)
    memory_relevance: float = Field(ge=0.0, le=1.0)

    @field_validator("sensitive_categories")
    @classmethod
    def enforce_sensitive_category_floor(
        cls,
        value: tuple[SensitiveCategory, ...],
        info: ValidationInfo,
    ) -> tuple[SensitiveCategory, ...]:
        """Make model-supplied sensitive categories conservatively authoritative."""
        if value and info.data.get("sensitivity") in {
            Sensitivity.PUBLIC,
            Sensitivity.PERSONAL,
        }:
            info.data["sensitivity"] = Sensitivity.SENSITIVE
        return value

    @model_validator(mode="after")
    def validate_collections(self) -> Self:
        for label, values in (
            ("projects", self.projects),
            ("entities", self.entities),
            ("sensitive categories", self.sensitive_categories),
        ):
            if len(set(values)) != len(values):
                raise ValueError(f"{label} must be unique")
        if (
            self.sensitivity
            in {
                Sensitivity.SENSITIVE,
                Sensitivity.FORBIDDEN,
            }
            and not self.sensitive_categories
        ):
            raise ValueError("sensitive content requires a sensitive category")
        return self


class LocalModelRuntimeStatus(BaseModel):
    """Non-sensitive availability and identity for one local model runtime."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    provider: Literal["ollama"] = "ollama"
    endpoint: str
    runtime_available: bool
    runtime_version: str | None = Field(default=None, max_length=64)
    model: str = Field(min_length=1, max_length=255)
    model_available: bool
    model_digest: str | None = Field(default=None, max_length=128)
    reason_code: str | None = Field(default=None, max_length=64)

    _loopback_endpoint = field_validator("endpoint")(
        lambda value: LoopbackHttpEndpoint.parse(value).url
    )

    @model_validator(mode="after")
    def validate_status(self) -> Self:
        if self.model_available and not self.runtime_available:
            raise ValueError("a model cannot be available without its runtime")
        if self.model_available != (self.model_digest is not None):
            raise ValueError("available model status requires exactly one digest")
        if self.runtime_available != (self.runtime_version is not None):
            raise ValueError("available runtime status requires exactly one version")
        if (self.runtime_available and self.model_available) == (
            self.reason_code is not None
        ):
            raise ValueError("healthy status must not have a reason code")
        return self


class LocalModelExecution(BaseModel):
    """Validated interpretation with inspectable, content-free provenance."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    request_id: UUID
    source_observation_ids: tuple[UUID, ...] = Field(min_length=1, max_length=32)
    provider: Literal["ollama"] = "ollama"
    endpoint: str
    runtime_version: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=255)
    model_digest: str = Field(min_length=1, max_length=128)
    prompt_version: PromptVersion = PROMPT_VERSION
    output_schema_version: Literal["model-interpretation-v1"] = (
        "model-interpretation-v1"
    )
    image_sha256: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]+$")
    interpretation: ModelInterpretation = Field(repr=False)
    started_at: datetime
    ended_at: datetime
    wall_duration_ms: int = Field(ge=0)
    runtime_duration_ms: int | None = Field(default=None, ge=0)
    load_duration_ms: int | None = Field(default=None, ge=0)
    prompt_eval_count: int | None = Field(default=None, ge=0)
    eval_count: int | None = Field(default=None, ge=0)

    _loopback_endpoint = field_validator("endpoint")(
        lambda value: LoopbackHttpEndpoint.parse(value).url
    )
    _utc_timestamps = field_validator("started_at", "ended_at")(require_aware_utc)

    @model_validator(mode="after")
    def validate_execution(self) -> Self:
        if self.started_at > self.ended_at:
            raise ValueError("model execution cannot end before it starts")
        if len(set(self.source_observation_ids)) != len(self.source_observation_ids):
            raise ValueError("model execution source identifiers must be unique")
        return self


class ModelTransformationStatus(StrEnum):
    """Lifecycle of one replayable logical model transformation."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    ABANDONED = "abandoned"


class ModelAttemptInvocation(StrEnum):
    NOT_INVOKED = "not_invoked"
    INVOKED = "invoked"
    UNKNOWN = "unknown"


class ModelAttempt(BaseModel):
    """Content-free immutable outcome for one transformation attempt."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    transformation_id: UUID
    processing_run_id: UUID
    attempt_number: int = Field(ge=1, le=10)
    invocation: ModelAttemptInvocation
    status: ModelTransformationStatus
    error_code: str | None = Field(
        default=None,
        max_length=64,
        pattern=r"^[a-z0-9_]+$",
    )
    started_at: datetime
    ended_at: datetime

    _utc_timestamps = field_validator("started_at", "ended_at")(require_aware_utc)

    @model_validator(mode="after")
    def validate_attempt(self) -> Self:
        if self.status in {
            ModelTransformationStatus.PENDING,
            ModelTransformationStatus.RUNNING,
        }:
            raise ValueError("model attempts require a terminal status")
        failed = self.status in {
            ModelTransformationStatus.FAILED,
            ModelTransformationStatus.ABANDONED,
        }
        if failed != (self.error_code is not None):
            raise ValueError("failed model attempts require exactly one error code")
        if self.ended_at < self.started_at:
            raise ValueError("model attempt cannot end before it starts")
        return self


class ModelTransformation(BaseModel):
    """Persistent local-model work item and validated enriched output."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    id: UUID
    idempotency_key: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]+$")
    source_observation_ids: tuple[UUID, ...] = Field(min_length=1, max_length=32)
    provider: Literal["ollama"] = "ollama"
    endpoint: str
    configured_model: str = Field(min_length=1, max_length=255)
    runtime_version: str | None = Field(default=None, max_length=64)
    resolved_model: str | None = Field(default=None, max_length=255)
    model_digest: str | None = Field(default=None, max_length=128)
    prompt_version: PromptVersion = PROMPT_VERSION
    output_schema_version: Literal["model-interpretation-v1"] = (
        "model-interpretation-v1"
    )
    image_sha256: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]+$")
    status: ModelTransformationStatus = ModelTransformationStatus.PENDING
    interpretation: ModelInterpretation | None = Field(default=None, repr=False)
    attempt_count: int = Field(default=0, ge=0)
    started_at: datetime | None = None
    ended_at: datetime | None = None
    wall_duration_ms: int | None = Field(default=None, ge=0)
    runtime_duration_ms: int | None = Field(default=None, ge=0)
    load_duration_ms: int | None = Field(default=None, ge=0)
    prompt_eval_count: int | None = Field(default=None, ge=0)
    eval_count: int | None = Field(default=None, ge=0)
    next_attempt_at: datetime | None = None
    last_error_code: str | None = Field(
        default=None,
        max_length=64,
        pattern=r"^[a-z0-9_]+$",
    )
    created_at: datetime
    updated_at: datetime

    _loopback_endpoint = field_validator("endpoint")(
        lambda value: LoopbackHttpEndpoint.parse(value).url
    )
    _utc_timestamps = field_validator(
        "started_at",
        "ended_at",
        "next_attempt_at",
        "created_at",
        "updated_at",
    )(lambda value: None if value is None else require_aware_utc(value))

    @model_validator(mode="after")
    def validate_transformation(self) -> Self:
        if len(set(self.source_observation_ids)) != len(self.source_observation_ids):
            raise ValueError("model transformation source identifiers must be unique")
        if self.updated_at < self.created_at:
            raise ValueError("model transformation cannot update before creation")
        identity = (
            self.runtime_version,
            self.resolved_model,
            self.model_digest,
        )
        metrics = (
            self.wall_duration_ms,
            self.runtime_duration_ms,
            self.load_duration_ms,
            self.prompt_eval_count,
            self.eval_count,
        )
        if self.status is ModelTransformationStatus.PENDING:
            if (
                any(value is not None for value in identity)
                or self.attempt_count != 0
                or self.started_at is not None
                or self.ended_at is not None
                or self.interpretation is not None
                or any(value is not None for value in metrics)
                or self.last_error_code is not None
                or self.next_attempt_at is None
            ):
                raise ValueError("pending model transformation state is inconsistent")
        elif self.status is ModelTransformationStatus.RUNNING:
            if (
                any(value is None for value in identity)
                or self.attempt_count < 1
                or self.started_at is None
                or self.ended_at is not None
                or self.interpretation is not None
                or any(value is not None for value in metrics)
                or self.next_attempt_at is not None
                or self.last_error_code is not None
            ):
                raise ValueError("running model transformation state is inconsistent")
        elif self.status is ModelTransformationStatus.SUCCEEDED:
            if (
                any(value is None for value in identity)
                or self.attempt_count < 1
                or self.started_at is None
                or self.ended_at is None
                or self.interpretation is None
                or self.wall_duration_ms is None
                or self.next_attempt_at is not None
                or self.last_error_code is not None
            ):
                raise ValueError("succeeded model transformation state is inconsistent")
        elif self.status is ModelTransformationStatus.FAILED:
            if (
                any(value is None for value in identity)
                or self.attempt_count < 1
                or self.started_at is None
                or self.ended_at is None
                or self.interpretation is not None
                or any(value is not None for value in metrics)
                or self.next_attempt_at is None
                or self.last_error_code is None
            ):
                raise ValueError("failed model transformation state is inconsistent")
        elif (
            any(value is None for value in identity)
            or self.attempt_count < 1
            or self.started_at is None
            or self.ended_at is None
            or self.interpretation is not None
            or any(value is not None for value in metrics)
            or self.next_attempt_at is not None
            or self.last_error_code is None
        ):
            raise ValueError("abandoned model transformation state is inconsistent")
        if (
            self.started_at is not None
            and self.ended_at is not None
            and self.started_at > self.ended_at
        ):
            raise ValueError("model transformation cannot end before it starts")
        if self.next_attempt_at is not None and self.next_attempt_at < self.updated_at:
            raise ValueError("next model attempt cannot precede the current update")
        return self

    def start(
        self,
        *,
        runtime: LocalModelRuntimeStatus,
        started_at: datetime,
    ) -> Self:
        """Begin or retry this logical transformation with a proven model."""
        if self.status not in {
            ModelTransformationStatus.PENDING,
            ModelTransformationStatus.FAILED,
        }:
            raise ValueError("only pending or failed model work can start")
        if (
            not runtime.runtime_available
            or runtime.runtime_version is None
            or not runtime.model_available
            or runtime.model_digest is None
            or runtime.provider != self.provider
            or runtime.endpoint != self.endpoint
            or runtime.model != self.configured_model
            or (
                self.model_digest is not None
                and runtime.model_digest != self.model_digest
            )
        ):
            raise ValueError("model runtime does not match the transformation")
        started = require_aware_utc(started_at)
        if self.next_attempt_at is not None and started < self.next_attempt_at:
            raise ValueError("model transformation retry is not due")
        return type(self).model_validate(
            self.model_dump()
            | {
                "runtime_version": runtime.runtime_version,
                "resolved_model": runtime.model,
                "model_digest": runtime.model_digest,
                "status": ModelTransformationStatus.RUNNING,
                "attempt_count": self.attempt_count + 1,
                "started_at": started,
                "ended_at": None,
                "wall_duration_ms": None,
                "runtime_duration_ms": None,
                "load_duration_ms": None,
                "prompt_eval_count": None,
                "eval_count": None,
                "next_attempt_at": None,
                "last_error_code": None,
                "updated_at": started,
            }
        )

    def succeed(self, execution: LocalModelExecution) -> Self:
        """Accept one execution only when all provenance matches this work item."""
        if self.status is not ModelTransformationStatus.RUNNING:
            raise ValueError("only running model work can succeed")
        if (
            execution.request_id != self.id
            or execution.source_observation_ids != self.source_observation_ids
            or execution.provider != self.provider
            or execution.endpoint != self.endpoint
            or execution.runtime_version != self.runtime_version
            or execution.model != self.resolved_model
            or execution.model_digest != self.model_digest
            or execution.prompt_version != self.prompt_version
            or execution.output_schema_version != self.output_schema_version
            or execution.image_sha256 != self.image_sha256
            or (self.started_at is not None and execution.started_at < self.started_at)
        ):
            raise ValueError("model execution provenance does not match its work item")
        return type(self).model_validate(
            self.model_dump()
            | {
                "status": ModelTransformationStatus.SUCCEEDED,
                "interpretation": execution.interpretation,
                "ended_at": execution.ended_at,
                "wall_duration_ms": execution.wall_duration_ms,
                "runtime_duration_ms": execution.runtime_duration_ms,
                "load_duration_ms": execution.load_duration_ms,
                "prompt_eval_count": execution.prompt_eval_count,
                "eval_count": execution.eval_count,
                "updated_at": execution.ended_at,
            }
        )

    def fail(
        self,
        *,
        ended_at: datetime,
        error_code: str,
        next_attempt_at: datetime,
    ) -> Self:
        """Record one safe failure while keeping bounded retry state."""
        if self.status is not ModelTransformationStatus.RUNNING:
            raise ValueError("only running model work can fail")
        ended = require_aware_utc(ended_at)
        next_attempt = require_aware_utc(next_attempt_at)
        return type(self).model_validate(
            self.model_dump()
            | {
                "status": ModelTransformationStatus.FAILED,
                "ended_at": ended,
                "wall_duration_ms": None,
                "runtime_duration_ms": None,
                "load_duration_ms": None,
                "prompt_eval_count": None,
                "eval_count": None,
                "next_attempt_at": next_attempt,
                "last_error_code": error_code,
                "updated_at": ended,
            }
        )

    def abandon(
        self,
        *,
        ended_at: datetime,
        error_code: str,
    ) -> Self:
        """Stop running or retrying a transformation without losing its audit."""
        if self.status not in {
            ModelTransformationStatus.RUNNING,
            ModelTransformationStatus.FAILED,
        }:
            raise ValueError("only running or failed model work can be abandoned")
        ended = require_aware_utc(ended_at)
        return type(self).model_validate(
            self.model_dump()
            | {
                "status": ModelTransformationStatus.ABANDONED,
                "ended_at": (
                    ended
                    if self.status is ModelTransformationStatus.RUNNING
                    else self.ended_at
                ),
                "wall_duration_ms": None,
                "runtime_duration_ms": None,
                "load_duration_ms": None,
                "prompt_eval_count": None,
                "eval_count": None,
                "next_attempt_at": None,
                "last_error_code": error_code,
                "updated_at": ended,
            }
        )


class ModelProvider(Protocol):
    """Mandatory local semantic interpretation boundary."""

    def status(self) -> LocalModelRuntimeStatus: ...

    def interpret(self, request: LocalModelRequest) -> LocalModelExecution: ...
