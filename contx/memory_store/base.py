"""Final-memory contracts owned by CONTX."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol


@dataclass(frozen=True, slots=True)
class MemoryAppendResult:
    """Stable identity returned after one durable memory append."""

    backend_id: str
    maintenance_required: bool = False


@dataclass(frozen=True, slots=True)
class MemoryWake:
    """One semantic context page plus separately transported backend status."""

    content: str
    complete: bool
    maintenance_required: bool = False
    technical_status: str | None = None
    snapshot: int | None = None
    next_part: int | None = None


@dataclass(frozen=True, slots=True)
class MemoryCompressionRequest:
    """One backend-generated, local-only summary request."""

    block: str
    prompt: str = field(repr=False)
    max_bytes: int


@dataclass(frozen=True, slots=True)
class MemoryMaintenance:
    """Bounded compression progress without hiding unfinished work."""

    completed_compressions: int
    complete: bool
    next_request: MemoryCompressionRequest | None = None


class MemoryCompressor(Protocol):
    """Generate one bounded summary without owning memory persistence."""

    def compress(self, request: MemoryCompressionRequest) -> str:
        """Return one evidence-backed summary line for the requested block."""


class MemoryCorrectionComposer(Protocol):
    """Turn an explicit replacement into one autonomous correction memory."""

    @property
    def provider(self) -> Literal["ollama"]: ...

    @property
    def endpoint(self) -> str: ...

    @property
    def model(self) -> str: ...

    @property
    def model_digest(self) -> str | None: ...

    @property
    def prompt_version(self) -> str: ...

    @property
    def output_schema_version(self) -> str: ...

    def compose(
        self,
        *,
        original: str,
        replacement: str,
        max_bytes: int,
    ) -> str:
        """Return the corrected current fact without owning persistence."""


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

    def maintain(
        self,
        compressor: MemoryCompressor,
        *,
        max_compressions: int,
    ) -> MemoryMaintenance:
        """Perform a bounded number of pending local compression steps."""

    def invalidate_summary(self, block: str) -> None:
        """Invalidate one incorrect summary while retaining raw memories."""
