"""Append-only, replayable decisions for multi-event memory candidates."""

from __future__ import annotations

from uuid import NAMESPACE_URL, UUID, uuid5

from contx.models import (
    CandidateDecision,
    CandidateDecisionStatus,
    Clock,
    MemoryCandidate,
)
from contx.models.common import build_idempotency_key

CANDIDATE_POLICY_VERSION = "candidate-policy-v1"
DEFAULT_ACCEPTANCE_THRESHOLD = 0.65
DEFAULT_MINIMUM_CONFIDENCE = 0.55
DEFAULT_MAXIMUM_AMBIGUITY = 0.45
DEFAULT_MAXIMUM_REDUNDANCY = 0.60


class TransparentCandidateWorker:
    """Evaluate candidates with ordered and externally visible policy reasons."""

    def __init__(
        self,
        *,
        clock: Clock,
        policy_version: str = CANDIDATE_POLICY_VERSION,
        acceptance_threshold: float = DEFAULT_ACCEPTANCE_THRESHOLD,
        minimum_confidence: float = DEFAULT_MINIMUM_CONFIDENCE,
        maximum_ambiguity: float = DEFAULT_MAXIMUM_AMBIGUITY,
        maximum_redundancy: float = DEFAULT_MAXIMUM_REDUNDANCY,
    ) -> None:
        normalized = policy_version.strip()
        if (
            not normalized
            or len(normalized) > 64
            or any(character in normalized for character in "\r\n")
        ):
            raise ValueError("candidate policy version is invalid")
        for label, value in {
            "acceptance threshold": acceptance_threshold,
            "minimum confidence": minimum_confidence,
            "maximum ambiguity": maximum_ambiguity,
            "maximum redundancy": maximum_redundancy,
        }.items():
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{label} must be between zero and one")
        self._clock = clock
        self.policy_version = normalized
        self.acceptance_threshold = acceptance_threshold
        self.minimum_confidence = minimum_confidence
        self.maximum_ambiguity = maximum_ambiguity
        self.maximum_redundancy = maximum_redundancy

    def evaluate(
        self,
        candidates: tuple[MemoryCandidate, ...],
        *,
        processing_run_id: UUID,
    ) -> tuple[CandidateDecision, ...]:
        return tuple(
            self._evaluate_one(candidate, processing_run_id=processing_run_id)
            for candidate in candidates
        )

    def _evaluate_one(
        self,
        candidate: MemoryCandidate,
        *,
        processing_run_id: UUID,
    ) -> CandidateDecision:
        status, reason = self._outcome(candidate)
        key = build_idempotency_key(
            "candidate-evaluation-v1",
            processing_run_id,
            self.policy_version,
            self.acceptance_threshold,
            self.minimum_confidence,
            self.maximum_ambiguity,
            self.maximum_redundancy,
            candidate.idempotency_key,
        )
        return CandidateDecision(
            id=uuid5(NAMESPACE_URL, f"contx:candidate-decision:{key}"),
            idempotency_key=key,
            candidate_id=candidate.id,
            processing_run_id=processing_run_id,
            policy_version=self.policy_version,
            acceptance_threshold=self.acceptance_threshold,
            status=status,
            reason=reason,
            created_at=self._clock.now(),
        )

    def _outcome(
        self,
        candidate: MemoryCandidate,
    ) -> tuple[CandidateDecisionStatus, str | None]:
        if candidate.source_type != "pattern":
            return (
                CandidateDecisionStatus.REJECTED,
                "missing_multi_event_pattern_provenance",
            )
        if not candidate.sensitivity.permits_durable_memory:
            return (
                CandidateDecisionStatus.REJECTED,
                "sensitivity_not_eligible_for_durable_memory",
            )
        if candidate.redundancy >= self.maximum_redundancy:
            return CandidateDecisionStatus.REJECTED, "redundant_candidate"
        if candidate.confidence < self.minimum_confidence:
            return CandidateDecisionStatus.DEFERRED, "insufficient_confidence"
        if candidate.ambiguity > self.maximum_ambiguity:
            return CandidateDecisionStatus.DEFERRED, "ambiguous_candidate"
        if candidate.score < self.acceptance_threshold:
            return CandidateDecisionStatus.REJECTED, "below_memory_threshold"
        return CandidateDecisionStatus.ACCEPTED, None
