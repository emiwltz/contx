"""Conservative multi-event pattern and candidate behavior."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from contx.candidates import PatternCandidateProducer
from contx.memory_worker import TransparentCandidateWorker
from contx.models import (
    ActivityTimeline,
    CandidateDecisionStatus,
    EpistemicStatus,
    EventType,
    MemoryCandidate,
    PatternStatus,
    PatternType,
    Sensitivity,
    TimelineBuild,
    TimelineEntry,
)
from contx.patterns import TemporalPatternEngine
from tests.helpers import FixedClock

START = datetime(2026, 7, 28, tzinfo=UTC)
END = START + timedelta(days=4)
BOUNDARY = START + timedelta(days=2)
TIMELINE_RUN_ID = UUID("10000000-0000-0000-0000-000000000001")
DECISION_RUN_ID = UUID("10000000-0000-0000-0000-000000000099")


def test_single_event_cannot_create_a_pattern() -> None:
    timeline = _timeline((_entry(1, project="Solo"),))
    engine = TemporalPatternEngine(clock=FixedClock(END))

    assert engine.detect(timeline, comparison_boundary=BOUNDARY) == ()


@pytest.mark.parametrize("project", ["Atlas", "Orion"])
def test_multi_day_evidence_produces_fused_candidate_without_fixture_coupling(
    project: str,
) -> None:
    timeline = _timeline(
        (
            _entry(1, project=project, start=START + timedelta(hours=9)),
            _entry(2, project=project, start=START + timedelta(days=2, hours=10)),
            _entry(3, project=project, start=START + timedelta(days=2, hours=12)),
        )
    )
    engine = TemporalPatternEngine(clock=FixedClock(END))

    patterns = engine.detect(timeline, comparison_boundary=BOUNDARY)
    candidates = PatternCandidateProducer(clock=FixedClock(END)).produce(patterns)

    assert {pattern.type for pattern in patterns} == {
        PatternType.PROJECT_RECURRENCE,
        PatternType.PROJECT_RESUMPTION,
        PatternType.ACTIVITY_INCREASE,
    }
    assert all(pattern.evidence_count >= 2 for pattern in patterns)
    assert all(pattern.source_event_ids for pattern in patterns)
    assert len(candidates) == 1
    candidate = candidates[0]
    assert project in candidate.text
    assert candidate.source_type == "pattern"
    assert set(candidate.source_ids) == {pattern.id for pattern in patterns}
    assert candidate.score >= 0.65


def test_pattern_expiration_is_computed_without_changing_replay_evidence() -> None:
    pattern = TemporalPatternEngine(
        clock=FixedClock(END),
        validity=timedelta(days=1),
    ).detect(
        _timeline(
            (
                _entry(1, project="Atlas"),
                _entry(2, project="Atlas", start=START + timedelta(hours=11)),
            )
        ),
        comparison_boundary=BOUNDARY,
    )[0]

    assert pattern.status_at(pattern.valid_until - timedelta(microseconds=1)) is (
        PatternStatus.ACTIVE
    )
    assert pattern.status_at(pattern.valid_until) is PatternStatus.EXPIRED
    assert pattern.status is PatternStatus.ACTIVE


@pytest.mark.parametrize(
    ("changes", "status", "reason"),
    [
        (
            {"source_type": "event"},
            CandidateDecisionStatus.REJECTED,
            "missing_multi_event_pattern_provenance",
        ),
        (
            {"sensitivity": Sensitivity.SENSITIVE},
            CandidateDecisionStatus.REJECTED,
            "sensitivity_not_eligible_for_durable_memory",
        ),
        (
            {"redundancy": 0.9},
            CandidateDecisionStatus.REJECTED,
            "redundant_candidate",
        ),
        (
            {"confidence": 0.2},
            CandidateDecisionStatus.DEFERRED,
            "insufficient_confidence",
        ),
        (
            {"ambiguity": 0.8},
            CandidateDecisionStatus.DEFERRED,
            "ambiguous_candidate",
        ),
        (
            {"score": 0.2},
            CandidateDecisionStatus.REJECTED,
            "below_memory_threshold",
        ),
    ],
)
def test_worker_explains_unsupported_candidates(
    changes: dict[str, object],
    status: CandidateDecisionStatus,
    reason: str,
) -> None:
    candidate = _candidate(**changes)

    decision = TransparentCandidateWorker(clock=FixedClock(END)).evaluate(
        (candidate,),
        processing_run_id=DECISION_RUN_ID,
    )[0]

    assert decision.status is status
    assert decision.reason == reason


def _timeline(entries: tuple[TimelineEntry, ...]) -> ActivityTimeline:
    return ActivityTimeline(
        build=TimelineBuild(
            processing_run_id=TIMELINE_RUN_ID,
            processing_version="session-events-v1",
            window_start=START,
            window_end=END,
            session_gap_seconds=600,
            max_session_duration_seconds=7200,
        ),
        entries=entries,
    )


def _entry(
    index: int,
    *,
    project: str,
    start: datetime | None = None,
) -> TimelineEntry:
    started_at = start or START + timedelta(hours=9)
    return TimelineEntry(
        event_id=UUID(f"20000000-0000-0000-0000-{index:012d}"),
        lineage_key=f"{index:x}" * 64,
        type=EventType.PROJECT_WORK,
        summary=f"Synthetic work segment {index}.",
        started_at=started_at,
        ended_at=started_at + timedelta(hours=1),
        valid_from=started_at,
        valid_until=started_at + timedelta(hours=1),
        epistemic_status=EpistemicStatus.INFERRED,
        confidence=0.9,
        sensitivity=Sensitivity.PERSONAL,
        projects=(project,),
        source_observation_ids=(UUID(f"30000000-0000-0000-0000-{index:012d}"),),
        processing_version="session-events-v1",
    )


def _candidate(**changes: object) -> MemoryCandidate:
    values: dict[str, object] = {
        "id": UUID("40000000-0000-0000-0000-000000000001"),
        "idempotency_key": "a" * 64,
        "text": "Work on Atlas resumed with recurring supporting evidence.",
        "source_type": "pattern",
        "source_ids": (UUID("50000000-0000-0000-0000-000000000001"),),
        "utility": 0.9,
        "importance": 0.85,
        "durability": 0.8,
        "novelty": 0.75,
        "recurrence": 0.8,
        "confidence": 0.8,
        "ambiguity": 0.2,
        "redundancy": 0.0,
        "sensitivity": Sensitivity.PERSONAL,
        "score": 0.8,
        "scoring_version": "pattern-candidates-v1",
        "created_at": END,
    }
    values.update(changes)
    return MemoryCandidate.model_validate(values)
