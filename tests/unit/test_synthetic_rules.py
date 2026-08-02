"""Focused synthetic rule tests."""

from datetime import UTC, datetime
from uuid import UUID

from contx.candidates.rules import VerticalSliceCandidateProducer
from contx.collectors.synthetic import SyntheticCollector
from contx.events.rules import VerticalSliceEventBuilder
from contx.memory_worker import ThresholdMemoryWorker
from contx.models import CandidateStatus, Observation
from tests.helpers import FixedClock, SequenceIdentifiers

NOW = datetime(2026, 8, 2, 10, 15, tzinfo=UTC)
IDS = tuple(UUID(int=index) for index in range(1, 20))


def test_default_fixture_is_exactly_replayable() -> None:
    first = _records()
    replay = _records()

    assert first == replay
    assert all(record.window_title is None for record in first)


def test_rules_reject_trivial_activity_without_lowering_threshold() -> None:
    identifiers = SequenceIdentifiers(IDS)
    clock = FixedClock(NOW)
    observations = SyntheticCollector.default(
        clock=clock, identifiers=identifiers
    ).collect()
    events = VerticalSliceEventBuilder(clock=clock, identifiers=identifiers).build(
        observations
    )
    candidates = VerticalSliceCandidateProducer(
        clock=clock, identifiers=identifiers
    ).produce(events)

    decisions = ThresholdMemoryWorker().decide(candidates, processed_at=NOW)

    accepted = [item for item in decisions if item.status is CandidateStatus.ACCEPTED]
    rejected = [item for item in decisions if item.status is CandidateStatus.REJECTED]
    assert len(accepted) == 1
    assert accepted[0].score >= 0.75
    assert len(rejected) == 1
    assert rejected[0].score < 0.75


def _records() -> tuple[Observation, ...]:
    return SyntheticCollector.default(
        clock=FixedClock(NOW), identifiers=SequenceIdentifiers(IDS)
    ).collect()
