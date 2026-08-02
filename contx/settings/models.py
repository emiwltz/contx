"""Validate CONTX configuration without exposing private input values."""

from __future__ import annotations

import os
import stat
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from contx.errors import ConfigurationError
from contx.settings.paths import RuntimePaths

RAW_RETENTION_ENV = "CONTX_RAW_RETENTION_HOURS"
WINDOW_TITLES_ENV = "CONTX_WINDOW_TITLES_ENABLED"


class CollectionSettings(BaseModel):
    """Collection controls that default to the safest initial behavior."""

    model_config = ConfigDict(extra="forbid", strict=True)

    raw_retention_hours: int = Field(default=48, ge=1, le=48)
    window_titles_enabled: bool = False
    background_collection_enabled: Literal[False] = False


class AppSettings(BaseModel):
    """Versioned CONTX configuration."""

    model_config = ConfigDict(extra="forbid", strict=True)

    config_version: Literal[1] = 1
    collection: CollectionSettings = Field(default_factory=CollectionSettings)


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
