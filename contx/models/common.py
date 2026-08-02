"""Canonical serialization and idempotency helpers."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import UUID


def require_aware_utc(value: datetime) -> datetime:
    """Reject naive timestamps and normalize aware values to UTC."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    return value.astimezone(UTC)


def format_utc(value: datetime) -> str:
    """Serialize one aware timestamp as fixed-width RFC 3339 UTC."""
    return (
        require_aware_utc(value)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def parse_utc(value: str) -> datetime:
    """Parse one persisted RFC 3339 timestamp."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return require_aware_utc(parsed)


def build_idempotency_key(namespace: str, *parts: Any) -> str:
    """Hash canonical typed inputs without machine-specific representation."""
    payload = json.dumps(
        {"namespace": namespace, "parts": parts},
        default=_json_default,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return format_utc(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Enum):
        return str(value.value)
    raise TypeError(f"unsupported idempotency value type: {type(value).__name__}")
