"""Memory worker contract."""

from datetime import datetime
from typing import Protocol

from contx.models import MemoryCandidate


class MemoryWorker(Protocol):
    """Make explicit candidate decisions without writing memory directly."""

    def decide(
        self,
        candidates: tuple[MemoryCandidate, ...],
        *,
        processed_at: datetime,
    ) -> tuple[MemoryCandidate, ...]: ...
