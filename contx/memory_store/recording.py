"""Deterministic in-process MemoryStore used by tests."""

from __future__ import annotations

import re

from contx.memory_store.base import (
    MemoryAppendResult,
    MemoryCompressor,
    MemoryMaintenance,
    MemoryWake,
)


class RecordingMemoryStore:
    """Record final memories without filesystem or subprocess dependencies."""

    def __init__(self) -> None:
        self._initialized = False
        self._entries: list[str] = []
        self._ids_by_key: dict[str, str] = {}
        self.invalidated_blocks: list[str] = []

    @property
    def entries(self) -> tuple[str, ...]:
        return tuple(self._entries)

    def initialize(self) -> None:
        self._initialized = True

    def append(self, text: str, *, idempotency_key: str) -> MemoryAppendResult:
        self._require_initialized()
        existing = self._ids_by_key.get(idempotency_key)
        if existing is not None:
            return MemoryAppendResult(backend_id=existing)
        backend_id = str(len(self._entries))
        self._entries.append(text)
        self._ids_by_key[idempotency_key] = backend_id
        return MemoryAppendResult(backend_id=backend_id)

    def wake(self, *, part: int = 1, snapshot: int | None = None) -> MemoryWake:
        self._require_initialized()
        if part != 1:
            raise ValueError("recording memory has only one page")
        if snapshot is not None and snapshot != len(self._entries):
            raise ValueError("recording memory snapshot does not exist")
        lines = tuple(
            f"#{index} 2026-08-02 {entry}" for index, entry in enumerate(self._entries)
        )
        content = "" if not lines else "\n".join(lines) + "\n"
        return MemoryWake(
            content=content,
            complete=True,
            technical_status="You are awake.\n",
            snapshot=len(self._entries),
        )

    def recall(self, pattern: str) -> str:
        self._require_initialized()
        expression = re.compile(pattern, re.IGNORECASE)
        matches = tuple(
            f"#{index} 2026-08-02 {entry}"
            for index, entry in enumerate(self._entries)
            if expression.search(entry)
        )
        if not matches:
            return "No match.\n"
        return "\n".join((*matches, f"{len(matches)} match.")) + "\n"

    def zoom(self, block: str) -> str:
        self._require_initialized()
        index = int(block.split("-", maxsplit=1)[0])
        return f"#{index} 2026-08-02 {self._entries[index]}\n"

    def maintain(
        self,
        compressor: MemoryCompressor,
        *,
        max_compressions: int,
    ) -> MemoryMaintenance:
        self._require_initialized()
        if max_compressions < 1:
            raise ValueError("maximum compressions must be positive")
        return MemoryMaintenance(completed_compressions=0, complete=True)

    def invalidate_summary(self, block: str) -> None:
        self._require_initialized()
        self.invalidated_blocks.append(block)

    def _require_initialized(self) -> None:
        if not self._initialized:
            raise RuntimeError("recording memory is not initialized")
