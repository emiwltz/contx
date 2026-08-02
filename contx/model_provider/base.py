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
    field_validator,
    model_validator,
)

from contx.models import ActivityState, Sensitivity
from contx.models.common import require_aware_utc

PROMPT_VERSION = "local-screen-v1"
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

    @model_validator(mode="after")
    def validate_collections(self) -> Self:
        for label, values in (
            ("projects", self.projects),
            ("entities", self.entities),
            ("sensitive categories", self.sensitive_categories),
        ):
            if len(set(values)) != len(values):
                raise ValueError(f"{label} must be unique")
        if self.sensitivity is Sensitivity.FORBIDDEN and not self.sensitive_categories:
            raise ValueError("forbidden content requires a sensitive category")
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
    prompt_version: Literal["local-screen-v1"] = "local-screen-v1"
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

    _utc_timestamps = field_validator("started_at", "ended_at")(require_aware_utc)

    @model_validator(mode="after")
    def validate_execution(self) -> Self:
        if self.started_at > self.ended_at:
            raise ValueError("model execution cannot end before it starts")
        if len(set(self.source_observation_ids)) != len(self.source_observation_ids):
            raise ValueError("model execution source identifiers must be unique")
        return self


class ModelProvider(Protocol):
    """Mandatory local semantic interpretation boundary."""

    def status(self) -> LocalModelRuntimeStatus: ...

    def interpret(self, request: LocalModelRequest) -> LocalModelExecution: ...
