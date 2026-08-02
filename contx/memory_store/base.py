"""Final-memory contracts owned by CONTX."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class MemoryAppendResult:
    """Stable identity returned after one durable memory append."""

    backend_id: str
    maintenance_required: bool = False


@dataclass(frozen=True, slots=True)
class MemoryWake:
    """One unmodified semantic context page from the memory backend."""

    content: str
    complete: bool
    maintenance_required: bool = False
    snapshot: int | None = None
    next_part: int | None = None


class MemoryStore(Protocol):
    """Replaceable final-memory boundary.

    Implementations own persistence and transport details. ``wake`` content is
    the semantic context itself and must not be supplemented by another CONTX
    context builder.
    """

    def initialize(self) -> None:
        """Create the explicitly selected memory identity if it is absent."""

    def append(self, text: str, *, idempotency_key: str) -> MemoryAppendResult:
        """Append one memory exactly once for a stable idempotency key."""

    def wake(self, *, part: int = 1, snapshot: int | None = None) -> MemoryWake:
        """Return one backend context page without semantic rewriting."""

    def recall(self, pattern: str) -> str:
        """Return the backend's direct search output."""

    def zoom(self, block: str) -> str:
        """Return the backend's direct tree-navigation output."""
