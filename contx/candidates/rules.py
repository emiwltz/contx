"""Transparent candidate scoring for the synthetic vertical slice."""

from contx.models import Clock, Event, IdentifierSource, MemoryCandidate
from contx.models.common import build_idempotency_key

CANDIDATE_PROCESSING_VERSION = "synthetic-candidates-v1"


class SyntheticCandidateProducer:
    """Produce one useful and one intentionally weak candidate."""

    def __init__(self, *, clock: Clock, identifiers: IdentifierSource) -> None:
        self._clock = clock
        self._identifiers = identifiers

    def produce(self, events: tuple[Event, ...]) -> tuple[MemoryCandidate, ...]:
        return tuple(self._produce_one(event) for event in events)

    def _produce_one(self, event: Event) -> MemoryCandidate:
        if event.type == "project_resumption" and "CONTX" in event.projects:
            text = (
                "Resume CONTX from its current implementation state; sustained "
                "project work resumed after a multi-day gap."
            )
            importance, durability, novelty = 0.95, 0.9, 0.85
        else:
            text = "A brief synthetic settings interaction occurred."
            importance, durability, novelty = 0.1, 0.05, 0.1
        score = round(
            importance * 0.35
            + durability * 0.25
            + novelty * 0.2
            + event.confidence * 0.2,
            4,
        )
        return MemoryCandidate(
            id=self._identifiers.new(),
            idempotency_key=build_idempotency_key(
                "memory-candidate-v1",
                CANDIDATE_PROCESSING_VERSION,
                event.idempotency_key,
            ),
            text=text,
            source_type=event.type,
            source_ids=(event.id,),
            importance=importance,
            durability=durability,
            novelty=novelty,
            confidence=event.confidence,
            sensitivity=event.sensitivity,
            score=score,
            created_at=self._clock.now(),
        )
