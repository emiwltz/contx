"""Derive content-free pilot counters from durable CONTX state."""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import datetime
from typing import Protocol

from sqlalchemy import Engine

from contx.application import ActiveMemoryWakeResult
from contx.db import session_scope
from contx.db.repositories import (
    ModelTransformationRepository,
    PipelineRepository,
    RawObservationRepository,
)
from contx.evaluation.models import TechnicalReview, TechnicalSnapshot
from contx.evaluation.pilot import PilotEvaluationError
from contx.model_provider import (
    ModelAttempt,
    ModelAttemptInvocation,
    ModelTransformationStatus,
)
from contx.models import Clock, MemoryCandidate, MemoryLink, Sensitivity
from contx.models.common import require_aware_utc

INVALID_OUTPUT_ERROR_CODES = frozenset(
    {"invalid_model_response", "model_protocol_error"}
)


class ActiveMemoryWakeReader(Protocol):
    """Read the exact first context page presented by the active projection."""

    def wake(
        self,
        *,
        part: int = 1,
        snapshot: int | None = None,
    ) -> ActiveMemoryWakeResult: ...


class PilotTechnicalSnapshotService:
    """Combine safe persistence-derived counters with explicit human review."""

    def __init__(
        self,
        *,
        engine: Engine,
        active_memory: ActiveMemoryWakeReader,
        clock: Clock,
        monotonic: Callable[[], float] = time.perf_counter,
    ) -> None:
        self._engine = engine
        self._active_memory = active_memory
        self._clock = clock
        self._monotonic = monotonic

    def capture(
        self,
        *,
        pilot_started_at: datetime,
        review: TechnicalReview,
    ) -> TechnicalSnapshot:
        """Capture one cumulative snapshot without collecting new activity."""
        since = require_aware_utc(pilot_started_at)
        captured_at = self._clock.now()
        if captured_at < since:
            raise PilotEvaluationError("Technical snapshot precedes the pilot start")

        started = self._monotonic()
        wake = self._active_memory.wake().wake
        wake_latency_ms = max(0.0, (self._monotonic() - started) * 1000)
        if not wake.complete and wake.next_part is None:
            raise PilotEvaluationError(
                "Active memory is unavailable until local maintenance completes"
            )
        active_context_bytes = len(wake.content.encode("utf-8"))

        with session_scope(self._engine) as session:
            memories = PipelineRepository(session).durable_memory_records_since(since)
            attempts = ModelTransformationRepository(session).attempts(since=since)
            raw_repository = RawObservationRepository(session)
            raw_records_past_retention = raw_repository.expired_count(at=captured_at)
            detected_excluded_captures = raw_repository.excluded_capture_count(
                since=since
            )

        accepted_memories = len(memories)
        _validate_review(review, accepted_memories=accepted_memories)
        if review.excluded_context_captures < detected_excluded_captures:
            raise PilotEvaluationError(
                "Reviewed excluded captures cannot be below persisted violations"
            )
        model_outputs, invalid_model_outputs, unknown_model_attempts = (
            _model_attempt_counts(attempts)
        )

        return TechnicalSnapshot(
            captured_at=captured_at,
            accepted_memories=accepted_memories,
            accepted_memories_with_provenance=sum(
                _has_complete_provenance(link, candidate)
                for link, candidate in memories
            ),
            materially_false_memories=review.materially_false_memories,
            irrelevant_memories=review.irrelevant_memories,
            duplicate_memories=review.duplicate_memories,
            manual_corrections=sum(
                candidate.source_type == "memory_correction"
                for _link, candidate in memories
            ),
            sensitive_promotions=sum(
                candidate.sensitivity in {Sensitivity.SENSITIVE, Sensitivity.FORBIDDEN}
                for _link, candidate in memories
            ),
            synthetic_secret_promotions=review.synthetic_secret_promotions,
            model_outputs=model_outputs,
            invalid_model_outputs=invalid_model_outputs,
            unknown_model_attempts=unknown_model_attempts,
            raw_records_past_retention=raw_records_past_retention,
            excluded_context_captures=review.excluded_context_captures,
            remote_user_content_transports=(review.remote_user_content_transports),
            active_context_bytes=active_context_bytes,
            wake_latency_ms=wake_latency_ms,
        )


def _has_complete_provenance(link: MemoryLink, candidate: MemoryCandidate) -> bool:
    provenance = link.provenance
    return (
        provenance.candidate_id == candidate.id
        and bool(provenance.event_ids)
        and bool(provenance.observation_ids)
    )


def _model_attempt_counts(attempts: tuple[ModelAttempt, ...]) -> tuple[int, int, int]:
    completed_outputs = 0
    invalid_outputs = 0
    unknown_attempts = 0
    for attempt in attempts:
        if attempt.invocation is ModelAttemptInvocation.UNKNOWN:
            unknown_attempts += 1
        if attempt.invocation is not ModelAttemptInvocation.INVOKED:
            continue
        if attempt.status is ModelTransformationStatus.SUCCEEDED:
            completed_outputs += 1
        elif attempt.error_code in INVALID_OUTPUT_ERROR_CODES:
            completed_outputs += 1
            invalid_outputs += 1
    return completed_outputs, invalid_outputs, unknown_attempts


def _validate_review(review: TechnicalReview, *, accepted_memories: int) -> None:
    for label, count in (
        ("materially false", review.materially_false_memories),
        ("irrelevant", review.irrelevant_memories),
        ("duplicate", review.duplicate_memories),
        ("synthetic-secret", review.synthetic_secret_promotions),
    ):
        if count > accepted_memories:
            raise PilotEvaluationError(
                f"Reviewed {label} memories cannot exceed accepted memories"
            )
