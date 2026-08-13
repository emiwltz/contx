"""Persistence-derived pilot technical evidence tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import Engine

from contx.application import ActiveMemoryProjectionResult, ActiveMemoryWakeResult
from contx.db import create_database_engine, session_scope, upgrade_database
from contx.db.repositories import PipelineRepository
from contx.evaluation import (
    PilotEvaluationError,
    PilotTechnicalSnapshotService,
    TechnicalReview,
)
from contx.evaluation.technical import _model_attempt_counts
from contx.memory_store import MemoryWake
from contx.model_provider import (
    ModelAttempt,
    ModelAttemptInvocation,
    ModelTransformationStatus,
)
from contx.models import (
    CandidateStatus,
    EpistemicStatus,
    Event,
    EventType,
    MemoryCandidate,
    MemoryLink,
    MemoryProvenance,
    Observation,
    Sensitivity,
    SourceType,
)
from tests.helpers import FixedClock

START = datetime(2026, 8, 4, 8, tzinfo=UTC)
CAPTURED_AT = START + timedelta(days=3)
OBSERVATION_ID = UUID("f0000000-0000-0000-0000-000000000001")
EVENT_ID = UUID("f0000000-0000-0000-0000-000000000002")
CANDIDATE_ID = UUID("f0000000-0000-0000-0000-000000000003")
MEMORY_ID = UUID("f0000000-0000-0000-0000-000000000004")


def test_snapshot_combines_derived_state_and_explicit_review(tmp_path: Path) -> None:
    database_path = tmp_path / "contx.db"
    upgrade_database(database_path)
    engine = create_database_engine(database_path)
    try:
        _seed_sensitive_memory_and_expired_excluded_capture(engine)
        ticks = iter((10.0, 10.025))

        snapshot = PilotTechnicalSnapshotService(
            engine=engine,
            active_memory=StaticActiveMemory("local context"),
            clock=FixedClock(CAPTURED_AT),
            monotonic=lambda: next(ticks),
        ).capture(
            pilot_started_at=START,
            review=_review(excluded_context_captures=1),
        )

        assert snapshot.accepted_memories == 1
        assert snapshot.accepted_memories_with_provenance == 1
        assert snapshot.sensitive_promotions == 1
        assert snapshot.raw_records_past_retention == 1
        assert snapshot.excluded_context_captures == 1
        assert snapshot.active_context_bytes == len(b"local context")
        assert snapshot.wake_latency_ms == pytest.approx(25.0)
    finally:
        engine.dispose()


def test_snapshot_rejects_review_below_detected_exclusion_violation(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "contx.db"
    upgrade_database(database_path)
    engine = create_database_engine(database_path)
    try:
        _seed_sensitive_memory_and_expired_excluded_capture(engine)

        with pytest.raises(PilotEvaluationError, match="persisted violations"):
            PilotTechnicalSnapshotService(
                engine=engine,
                active_memory=StaticActiveMemory(""),
                clock=FixedClock(CAPTURED_AT),
                monotonic=lambda: 1.0,
            ).capture(
                pilot_started_at=START,
                review=_review(excluded_context_captures=0),
            )
    finally:
        engine.dispose()


def test_model_attempt_counts_only_real_outputs() -> None:
    attempts = (
        _attempt(
            1, ModelAttemptInvocation.INVOKED, ModelTransformationStatus.SUCCEEDED
        ),
        _attempt(
            2,
            ModelAttemptInvocation.INVOKED,
            ModelTransformationStatus.FAILED,
            "invalid_model_response",
        ),
        _attempt(
            3,
            ModelAttemptInvocation.INVOKED,
            ModelTransformationStatus.FAILED,
            "runtime_unavailable",
        ),
        _attempt(
            4,
            ModelAttemptInvocation.UNKNOWN,
            ModelTransformationStatus.FAILED,
            "interrupted_model_attempt",
        ),
    )

    assert _model_attempt_counts(attempts) == (2, 1, 1)


class StaticActiveMemory:
    def __init__(self, content: str) -> None:
        self._content = content

    def wake(
        self,
        *,
        part: int = 1,
        snapshot: int | None = None,
    ) -> ActiveMemoryWakeResult:
        assert part == 1
        assert snapshot is None
        return ActiveMemoryWakeResult(
            wake=MemoryWake(content=self._content, complete=True, snapshot=42),
            projection=ActiveMemoryProjectionResult(
                fingerprint="a" * 64,
                generation="b" * 64,
                active_memory_count=1,
                rebuilt=False,
                completed_compressions=0,
            ),
        )


def _review(*, excluded_context_captures: int) -> TechnicalReview:
    return TechnicalReview(
        materially_false_memories=0,
        irrelevant_memories=0,
        duplicate_memories=0,
        synthetic_secret_promotions=0,
        excluded_context_captures=excluded_context_captures,
        remote_user_content_transports=0,
    )


def _seed_sensitive_memory_and_expired_excluded_capture(engine: Engine) -> None:
    observation = Observation(
        id=OBSERVATION_ID,
        idempotency_key="1" * 64,
        source_type=SourceType.SCREENSHOT,
        captured_at=START,
        app_name="Excluded",
        artifact_path="/private/tmp/excluded.png",
        content_hash="2" * 64,
        excluded=True,
        exclusion_reason="synthetic policy violation",
        expires_at=START + timedelta(hours=48),
        created_at=START,
    )
    event = Event(
        id=EVENT_ID,
        idempotency_key="3" * 64,
        lineage_key="4" * 64,
        type=EventType.PROJECT_WORK,
        summary="Synthetic sensitive event.",
        facts={},
        started_at=START,
        ended_at=START + timedelta(minutes=5),
        valid_from=START,
        valid_until=START + timedelta(minutes=5),
        epistemic_status=EpistemicStatus.INFERRED,
        confidence=0.8,
        sensitivity=Sensitivity.SENSITIVE,
        source_observation_ids=(observation.id,),
        processing_version="test-v1",
        created_at=START,
        updated_at=START,
    )
    candidate = MemoryCandidate(
        id=CANDIDATE_ID,
        idempotency_key="5" * 64,
        text="Synthetic sensitive memory.",
        source_type="event",
        source_ids=(event.id,),
        utility=0.8,
        importance=0.8,
        durability=0.8,
        novelty=0.8,
        confidence=0.8,
        sensitivity=Sensitivity.SENSITIVE,
        score=0.8,
        status=CandidateStatus.STORED,
        created_at=START,
        processed_at=START,
    )
    link = MemoryLink(
        id=MEMORY_ID,
        memory_backend_id="memory-1",
        candidate_id=candidate.id,
        provenance=MemoryProvenance(
            candidate_id=candidate.id,
            event_ids=(event.id,),
            observation_ids=(observation.id,),
        ),
        confidence=0.8,
        created_at=START,
    )
    with session_scope(engine) as session:
        repository = PipelineRepository(session)
        repository.save_observation(observation)
        repository.save_event(event)
        repository.save_candidate(candidate)
        repository.save_memory_link(link)


def _attempt(
    number: int,
    invocation: ModelAttemptInvocation,
    status: ModelTransformationStatus,
    error_code: str | None = None,
) -> ModelAttempt:
    return ModelAttempt(
        transformation_id=UUID(f"f0000000-0000-0000-0001-{number:012d}"),
        processing_run_id=UUID(f"f0000000-0000-0000-0002-{number:012d}"),
        attempt_number=1,
        invocation=invocation,
        status=status,
        error_code=error_code,
        started_at=START + timedelta(minutes=number),
        ended_at=START + timedelta(minutes=number, seconds=1),
    )
