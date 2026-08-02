"""Memory candidate production contract."""

from typing import Protocol

from contx.models import Event, MemoryCandidate


class CandidateProducer(Protocol):
    """Produce transparent memory candidates from events."""

    def produce(self, events: tuple[Event, ...]) -> tuple[MemoryCandidate, ...]: ...
