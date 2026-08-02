"""Temporary raw-artifact contracts."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from contx.models.common import require_aware_utc


class RawArtifact(BaseModel):
    """One private temporary file with an enforced expiry."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    path: Path
    content_hash: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]+$")
    size_bytes: int = Field(ge=0)
    captured_at: datetime
    expires_at: datetime

    _utc_timestamps = field_validator("captured_at", "expires_at")(require_aware_utc)

    @model_validator(mode="after")
    def validate_retention(self) -> RawArtifact:
        retention = self.expires_at - self.captured_at
        if not timedelta(0) < retention <= timedelta(hours=48):
            raise ValueError("raw artifact retention must be between zero and 48 hours")
        return self


class RawStore(Protocol):
    def initialize(self) -> None: ...

    def write(
        self,
        payload: bytes,
        *,
        artifact_id: UUID,
        suffix: str,
        captured_at: datetime,
        retention: timedelta,
    ) -> RawArtifact: ...

    def delete(self, path: Path) -> bool: ...

    def size(self, path: Path) -> int: ...

    def usage_bytes(self) -> int: ...

    def list_paths(self) -> tuple[Path, ...]: ...
