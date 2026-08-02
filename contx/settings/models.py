"""Validate CONTX configuration without exposing private input values."""

from __future__ import annotations

import os
import stat
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from contx.errors import ConfigurationError
from contx.model_provider import DEFAULT_ENDPOINT, DEFAULT_MODEL, LoopbackHttpEndpoint
from contx.settings.paths import RuntimePaths

RAW_RETENTION_ENV = "CONTX_RAW_RETENTION_HOURS"
WINDOW_TITLES_ENV = "CONTX_WINDOW_TITLES_ENABLED"


class CollectionSettings(BaseModel):
    """Collection controls that default to the safest initial behavior."""

    model_config = ConfigDict(extra="forbid", strict=True)

    raw_retention_hours: int = Field(default=48, ge=1, le=48)
    window_titles_enabled: bool = False
    background_collection_enabled: bool = False
    retain_excluded_activity: bool = False
    poll_interval_seconds: float = Field(default=1.0, ge=0.25, le=10.0)
    segment_max_duration_seconds: int = Field(default=60, ge=5, le=300)
    idle_threshold_seconds: int = Field(default=300, ge=30, le=3600)
    purge_interval_seconds: int = Field(default=900, ge=60, le=3600)
    screenshots_enabled: bool = False
    screenshot_min_interval_seconds: int = Field(default=15, ge=5, le=300)
    screenshot_max_interval_seconds: int = Field(default=120, ge=15, le=1800)
    raw_disk_budget_mb: int = Field(default=5120, ge=64, le=5120)

    @model_validator(mode="after")
    def validate_screenshot_intervals(self) -> Self:
        if self.screenshot_max_interval_seconds < self.screenshot_min_interval_seconds:
            raise ValueError(
                "screenshot maximum interval must not be below the minimum"
            )
        return self


class ModelSettings(BaseModel):
    """Mandatory local multimodal model configuration."""

    model_config = ConfigDict(extra="forbid", strict=True)

    provider: Literal["ollama"] = "ollama"
    endpoint: str = DEFAULT_ENDPOINT
    model_name: str = Field(default=DEFAULT_MODEL, min_length=1, max_length=255)
    timeout_seconds: float = Field(default=120.0, ge=0.1, le=600.0)
    keep_alive: str = Field(default="5m", min_length=1, max_length=32)
    context_tokens: int = Field(default=8192, ge=2048, le=32768)
    max_output_tokens: int = Field(default=512, ge=128, le=2048)
    max_image_mb: int = Field(default=20, ge=1, le=50)
    max_response_kb: int = Field(default=1024, ge=1, le=10240)

    @field_validator("endpoint")
    @classmethod
    def validate_endpoint(cls, value: str) -> str:
        try:
            return LoopbackHttpEndpoint.parse(value).url
        except ValueError as error:
            raise ValueError("endpoint must be a literal loopback HTTP URL") from error

    @field_validator("model_name", "keep_alive")
    @classmethod
    def validate_single_line_value(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or any(character in normalized for character in "\r\n"):
            raise ValueError("value must be one non-empty line")
        return normalized


class EventSettings(BaseModel):
    """Deterministic activity-session policy for replayable timelines."""

    model_config = ConfigDict(extra="forbid", strict=True)

    session_gap_seconds: int = Field(default=600, ge=30, le=3600)
    max_session_duration_seconds: int = Field(default=7200, ge=300, le=14400)

    @model_validator(mode="after")
    def validate_session_limits(self) -> Self:
        if self.max_session_duration_seconds <= self.session_gap_seconds:
            raise ValueError("maximum session duration must exceed the session gap")
        return self


class MemorySettings(BaseModel):
    """Final-memory context and bounded local maintenance policy."""

    model_config = ConfigDict(extra="forbid", strict=True)

    wake_budget_bytes: int = Field(default=20000, ge=4096, le=32768)
    max_compressions_per_cycle: int = Field(default=4, ge=1, le=32)


class AppSettings(BaseModel):
    """Versioned CONTX configuration."""

    model_config = ConfigDict(extra="forbid", strict=True)

    config_version: int = Field(default=1, ge=1, le=1)
    collection: CollectionSettings = Field(default_factory=CollectionSettings)
    model: ModelSettings = Field(default_factory=ModelSettings)
    events: EventSettings = Field(default_factory=EventSettings)
    memory: MemorySettings = Field(default_factory=MemorySettings)


def load_settings(
    paths: RuntimePaths,
    environ: Mapping[str, str] | None = None,
) -> AppSettings:
    """Load defaults, then TOML, then explicit environment overrides."""
    environment = os.environ if environ is None else environ
    data = _read_config(paths.config_file)
    configured_collection = data.get("collection", {})
    if not isinstance(configured_collection, dict):
        raise ConfigurationError(
            "Invalid CONTX configuration: collection must be a table"
        )
    collection = dict(configured_collection)

    if RAW_RETENTION_ENV in environment:
        try:
            collection["raw_retention_hours"] = int(environment[RAW_RETENTION_ENV])
        except ValueError as error:
            raise ConfigurationError(
                f"{RAW_RETENTION_ENV} must be an integer"
            ) from error
    if WINDOW_TITLES_ENV in environment:
        collection["window_titles_enabled"] = _parse_boolean(
            WINDOW_TITLES_ENV, environment[WINDOW_TITLES_ENV]
        )
    if collection:
        data["collection"] = collection

    try:
        return AppSettings.model_validate(data)
    except ValidationError as error:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}"
            for item in error.errors(include_input=False, include_url=False)
        )
        raise ConfigurationError(f"Invalid CONTX configuration: {problems}") from error


def _read_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    if path.is_symlink():
        raise ConfigurationError(f"CONTX configuration must not be a symlink: {path}")
    try:
        file_status = path.stat()
    except OSError as error:
        raise ConfigurationError(
            f"Cannot inspect CONTX configuration: {path}"
        ) from error
    if not stat.S_ISREG(file_status.st_mode):
        raise ConfigurationError(f"CONTX configuration is not a regular file: {path}")
    if stat.S_IMODE(file_status.st_mode) & 0o077:
        raise ConfigurationError(
            f"CONTX configuration permissions are too broad: {path}; expected 0600"
        )
    try:
        with path.open("rb") as config_file:
            parsed = tomllib.load(config_file)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ConfigurationError(f"Cannot read CONTX configuration: {path}") from error
    return dict(parsed)


def _parse_boolean(name: str, value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(f"{name} must be a boolean")
