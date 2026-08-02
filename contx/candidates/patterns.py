"""Evidence-preserving fusion and scoring of multi-event patterns."""

from __future__ import annotations

from collections import defaultdict
from types import MappingProxyType
from uuid import NAMESPACE_URL, uuid5

from contx.models import (
    Clock,
    EpistemicStatus,
    MemoryCandidate,
    Pattern,
    PatternStatus,
    PatternType,
    Sensitivity,
)
from contx.models.common import build_idempotency_key

PATTERN_CANDIDATE_VERSION = "pattern-candidates-v1"

_SCORING_WEIGHTS = {
    "utility": 0.22,
    "importance": 0.18,
    "durability": 0.16,
    "novelty": 0.14,
    "recurrence": 0.15,
    "confidence": 0.15,
    "ambiguity": -0.15,
    "redundancy": -0.20,
    "sensitivity": -0.10,
}
SCORING_WEIGHTS = MappingProxyType(_SCORING_WEIGHTS)


class PatternCandidateProducer:
    """Fuse compatible patterns and create short autonomous candidates."""

    def __init__(
        self,
        *,
        clock: Clock,
        processing_version: str = PATTERN_CANDIDATE_VERSION,
    ) -> None:
        normalized = processing_version.strip()
        if (
            not normalized
            or len(normalized) > 64
            or any(character in normalized for character in "\r\n")
        ):
            raise ValueError("candidate processing version is invalid")
        self._clock = clock
        self.processing_version = normalized

    @property
    def scoring_weights(self) -> dict[str, float]:
        """Return a persistable copy of the explicit scoring policy."""
        return dict(SCORING_WEIGHTS)

    def produce(self, patterns: tuple[Pattern, ...]) -> tuple[MemoryCandidate, ...]:
        grouped: dict[str, list[Pattern]] = defaultdict(list)
        labels: dict[str, str] = {}
        now = self._clock.now()
        for pattern in patterns:
            if pattern.status_at(now) is not PatternStatus.ACTIVE:
                continue
            for project in pattern.projects:
                key = project.casefold()
                labels.setdefault(key, project)
                grouped[key].append(pattern)

        candidates = (
            self._produce_one(
                labels[key],
                tuple(
                    sorted(
                        {item.id: item for item in grouped[key]}.values(),
                        key=lambda item: (item.type.value, str(item.id)),
                    )
                ),
            )
            for key in sorted(grouped)
        )
        return tuple(candidates)

    def _produce_one(
        self,
        project: str,
        patterns: tuple[Pattern, ...],
    ) -> MemoryCandidate:
        utility, importance, durability, novelty, recurrence = _quality(patterns)
        confidence = round(min(pattern.confidence for pattern in patterns), 4)
        ambiguity = round(
            min(
                1.0,
                max(
                    1.0 - confidence,
                    0.35
                    if any(
                        pattern.epistemic_status is EpistemicStatus.HYPOTHETICAL
                        for pattern in patterns
                    )
                    else 0.05,
                ),
            ),
            4,
        )
        redundancy = 0.0
        sensitivity = max(
            (pattern.sensitivity for pattern in patterns),
            key=_sensitivity_rank,
        )
        score = _score(
            utility=utility,
            importance=importance,
            durability=durability,
            novelty=novelty,
            recurrence=recurrence,
            confidence=confidence,
            ambiguity=ambiguity,
            redundancy=redundancy,
            sensitivity=sensitivity,
        )
        source_ids = tuple(pattern.id for pattern in patterns)
        key = build_idempotency_key(
            "pattern-memory-candidate-v1",
            self.processing_version,
            project.casefold(),
            tuple(pattern.idempotency_key for pattern in patterns),
            self.scoring_weights,
        )
        return MemoryCandidate(
            id=uuid5(NAMESPACE_URL, f"contx:pattern-candidate:{key}"),
            idempotency_key=key,
            text=_candidate_text(project, patterns),
            source_type="pattern",
            source_ids=source_ids,
            utility=utility,
            importance=importance,
            durability=durability,
            novelty=novelty,
            recurrence=recurrence,
            confidence=confidence,
            ambiguity=ambiguity,
            redundancy=redundancy,
            sensitivity=sensitivity,
            score=score,
            scoring_version=self.processing_version,
            created_at=self._clock.now(),
        )


def _quality(patterns: tuple[Pattern, ...]) -> tuple[float, float, float, float, float]:
    types = {pattern.type for pattern in patterns}
    evidence_count = len(
        {event_id for pattern in patterns for event_id in pattern.source_event_ids}
    )
    if PatternType.PROJECT_RESUMPTION in types:
        base = (0.92, 0.86, 0.82, 0.78, 0.76)
    elif types & {
        PatternType.ACTIVITY_INCREASE,
        PatternType.ACTIVITY_DECREASE,
        PatternType.NEW_REPEATED_ACTIVITY,
    }:
        base = (0.82, 0.76, 0.68, 0.82, 0.66)
    else:
        base = (0.78, 0.72, 0.82, 0.58, 0.72)
    evidence_bonus = min(0.12, max(0, evidence_count - 2) * 0.02)
    utility, importance, durability, novelty, recurrence = (
        round(min(1.0, value + evidence_bonus), 4) for value in base
    )
    return utility, importance, durability, novelty, recurrence


def _candidate_text(project: str, patterns: tuple[Pattern, ...]) -> str:
    types = {pattern.type for pattern in patterns}
    event_count = len(
        {event_id for pattern in patterns for event_id in pattern.source_event_ids}
    )
    if PatternType.PROJECT_RESUMPTION in types:
        statement = f"Work on {project} resumed after a sustained inactivity gap"
    elif PatternType.ACTIVITY_INCREASE in types:
        statement = f"Time devoted to {project} increased materially"
    elif PatternType.ACTIVITY_DECREASE in types:
        statement = f"Time devoted to {project} decreased materially"
    elif PatternType.NEW_REPEATED_ACTIVITY in types:
        statement = f"{project} became a new repeated activity"
    else:
        statement = f"Work on {project} recurred"
    return f"{statement}, supported by {event_count} activity sessions."


def _score(
    *,
    utility: float,
    importance: float,
    durability: float,
    novelty: float,
    recurrence: float,
    confidence: float,
    ambiguity: float,
    redundancy: float,
    sensitivity: Sensitivity,
) -> float:
    values = {
        "utility": utility,
        "importance": importance,
        "durability": durability,
        "novelty": novelty,
        "recurrence": recurrence,
        "confidence": confidence,
        "ambiguity": ambiguity,
        "redundancy": redundancy,
        "sensitivity": _sensitivity_risk(sensitivity),
    }
    return round(
        min(
            1.0,
            max(
                0.0,
                sum(values[name] * weight for name, weight in SCORING_WEIGHTS.items()),
            ),
        ),
        4,
    )


def _sensitivity_risk(value: Sensitivity) -> float:
    return {
        Sensitivity.PUBLIC: 0.0,
        Sensitivity.PERSONAL: 0.25,
        Sensitivity.SENSITIVE: 1.0,
        Sensitivity.FORBIDDEN: 1.0,
    }[value]


def _sensitivity_rank(value: Sensitivity) -> int:
    return {
        Sensitivity.PUBLIC: 0,
        Sensitivity.PERSONAL: 1,
        Sensitivity.SENSITIVE: 2,
        Sensitivity.FORBIDDEN: 3,
    }[value]
