"""Domain record invariant tests."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from pydantic import ValidationError

from contx.models import (
    CandidateStatus,
    EpistemicStatus,
    Event,
    MemoryCandidate,
    Observation,
    ProcessingRun,
    ProcessingRunStatus,
    Sensitivity,
    SourceType,
)
from contx.models.common import build_idempotency_key

ID_1 = UUID("00000000-0000-0000-0000-000000000001")
ID_2 = UUID("00000000-0000-0000-0000-000000000002")
NOW = datetime(2026, 8, 2, 10, 0, tzinfo=UTC)


def test_naive_timestamps_are_rejected() -> None:
    with pytest.raises(ValidationError, match="timestamp must include a timezone"):
        _observation(captured_at=datetime(2026, 8, 2, 10, 0))


def test_event_requires_unique_provenance() -> None:
    with pytest.raises(ValidationError, match="must be unique"):
        _event(source_observation_ids=(ID_1, ID_1))


def test_observation_retention_cannot_exceed_48_hours() -> None:
    with pytest.raises(ValidationError, match="must not exceed 48 hours"):
        _observation(expires_at=NOW + timedelta(hours=48, microseconds=1))


def test_candidate_decision_is_explicit_and_irreversible() -> None:
    candidate = _candidate()

    accepted = candidate.decide(CandidateStatus.ACCEPTED, processed_at=NOW)

    assert accepted.status is CandidateStatus.ACCEPTED
    assert accepted.processed_at == NOW
    with pytest.raises(ValueError, match="only pending"):
        accepted.decide(CandidateStatus.REJECTED, processed_at=NOW, reason="noise")


def test_rejected_candidate_requires_a_reason() -> None:
    with pytest.raises(ValidationError, match="require exactly one reason"):
        _candidate(status=CandidateStatus.REJECTED, processed_at=NOW)


def test_idempotency_is_canonical_and_namespaced() -> None:
    first = build_idempotency_key("observation", ID_1, NOW, {"b": 2, "a": 1})
    replay = build_idempotency_key("observation", ID_1, NOW, {"a": 1, "b": 2})
    other = build_idempotency_key("event", ID_1, NOW, {"a": 1, "b": 2})

    assert first == replay
    assert first != other
    assert len(first) == 64


def test_processing_failure_rejects_multiline_private_detail() -> None:
    run = ProcessingRun(
        id=ID_1,
        pipeline="vertical_slice",
        version="pipeline-v1",
        started_at=NOW,
    )

    with pytest.raises(ValidationError, match="single-line and sanitized"):
        run.fail(
            ended_at=NOW,
            error_code="collector_failed",
            error_summary="private first line\nprivate second line",
        )

    succeeded = run.succeed(ended_at=NOW, input_count=3, output_count=1)
    assert succeeded.status is ProcessingRunStatus.SUCCEEDED


def _observation(**changes: object) -> Observation:
    values: dict[str, object] = {
        "id": ID_1,
        "idempotency_key": "a" * 64,
        "source_type": SourceType.SYNTHETIC,
        "captured_at": NOW,
        "started_at": NOW,
        "ended_at": NOW + timedelta(minutes=10),
        "app_name": "Synthetic Editor",
        "app_bundle_id": "test.synthetic.editor",
        "expires_at": NOW + timedelta(hours=48),
        "created_at": NOW,
    }
    values.update(changes)
    return Observation.model_validate(values)


def _event(**changes: object) -> Event:
    values: dict[str, object] = {
        "id": ID_2,
        "idempotency_key": "b" * 64,
        "type": "project_work",
        "summary": "Worked on a synthetic project.",
        "facts": {"active_seconds": 600},
        "started_at": NOW,
        "ended_at": NOW + timedelta(minutes=10),
        "epistemic_status": EpistemicStatus.INFERRED,
        "confidence": 0.9,
        "sensitivity": Sensitivity.PERSONAL,
        "projects": ("CONTX",),
        "source_observation_ids": (ID_1,),
        "processing_version": "event-v1",
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(changes)
    return Event.model_validate(values)


def _candidate(**changes: object) -> MemoryCandidate:
    values: dict[str, object] = {
        "id": ID_1,
        "idempotency_key": "c" * 64,
        "text": "Resume CONTX from the persistence foundation.",
        "source_type": "event",
        "source_ids": (ID_2,),
        "importance": 0.9,
        "durability": 0.8,
        "novelty": 0.8,
        "confidence": 0.9,
        "sensitivity": Sensitivity.PERSONAL,
        "score": 0.86,
        "created_at": NOW,
    }
    values.update(changes)
    return MemoryCandidate.model_validate(values)
