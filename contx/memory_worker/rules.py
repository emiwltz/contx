"""Conservative deterministic v0.0.1 memory policy."""

from datetime import datetime

from contx.models import CandidateStatus, MemoryCandidate


class ThresholdMemoryWorker:
    """Accept only an explicitly useful, high-scoring project resumption."""

    def __init__(self, *, acceptance_threshold: float = 0.75) -> None:
        if not 0.0 <= acceptance_threshold <= 1.0:
            raise ValueError("acceptance_threshold must be between zero and one")
        self._acceptance_threshold = acceptance_threshold

    def decide(
        self,
        candidates: tuple[MemoryCandidate, ...],
        *,
        processed_at: datetime,
    ) -> tuple[MemoryCandidate, ...]:
        decisions: list[MemoryCandidate] = []
        for candidate in candidates:
            if candidate.status is not CandidateStatus.PENDING:
                decisions.append(candidate)
            elif not candidate.sensitivity.permits_durable_memory:
                decisions.append(
                    candidate.decide(
                        CandidateStatus.REJECTED,
                        processed_at=processed_at,
                        reason="sensitivity_not_eligible_for_durable_memory",
                    )
                )
            elif (
                candidate.source_type == "project_resumption"
                and candidate.score >= self._acceptance_threshold
            ):
                decisions.append(
                    candidate.decide(
                        CandidateStatus.ACCEPTED, processed_at=processed_at
                    )
                )
            else:
                decisions.append(
                    candidate.decide(
                        CandidateStatus.REJECTED,
                        processed_at=processed_at,
                        reason="unsupported_or_below_memory_threshold",
                    )
                )
        return tuple(decisions)
