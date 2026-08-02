"""Frozen multi-day replay from timeline evidence through worker decisions."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from sqlalchemy import Engine

from contx.application import (
    ActivityTimelineService,
    CandidateEvaluationService,
    MemoryPromotionService,
    PatternAnalysisService,
    PatternCandidateService,
)
from contx.candidates import PatternCandidateProducer
from contx.db import create_database_engine, session_scope, upgrade_database
from contx.db.models import EventProcessingRunModel
from contx.db.repositories import (
    CandidateBuildRepository,
    CandidateEvaluationBuildRepository,
    PatternCandidateRepository,
    PatternRepository,
    PipelineRepository,
    TimelineBuildRepository,
)
from contx.events import ModelActivitySessionizer, SessionizedModelEventBuilder
from contx.memory_store import RecordingMemoryStore
from contx.memory_worker import TransparentCandidateWorker
from contx.models import (
    CandidateDecisionStatus,
    EpistemicStatus,
    Event,
    EventType,
    Observation,
    ProcessingRun,
    Sensitivity,
    SourceType,
    TimelineBuild,
)
from contx.patterns import TemporalPatternEngine
from tests.helpers import FixedClock, SequenceIdentifiers

START = datetime(2026, 7, 28, tzinfo=UTC)
END = START + timedelta(days=4)
BOUNDARY = START + timedelta(days=2)
NOW = END + timedelta(hours=1)
TIMELINE_RUN_ID = UUID("60000000-0000-0000-0000-000000000001")
PATTERN_RUN_ID = UUID("60000000-0000-0000-0000-000000000002")
PATTERN_REPLAY_RUN_ID = UUID("60000000-0000-0000-0000-000000000003")
PATTERN_V2_RUN_ID = UUID("60000000-0000-0000-0000-000000000004")
CANDIDATE_RUN_ID = UUID("60000000-0000-0000-0000-000000000005")
CANDIDATE_REPLAY_RUN_ID = UUID("60000000-0000-0000-0000-000000000006")
CANDIDATE_V2_RUN_ID = UUID("60000000-0000-0000-0000-000000000007")
LOW_EVALUATION_RUN_ID = UUID("60000000-0000-0000-0000-000000000008")
HIGH_EVALUATION_RUN_ID = UUID("60000000-0000-0000-0000-000000000009")
PROMOTION_RUN_ID = UUID("60000000-0000-0000-0000-000000000010")
PROMOTION_REPLAY_RUN_ID = UUID("60000000-0000-0000-0000-000000000011")
REJECTED_PROMOTION_RUN_ID = UUID("60000000-0000-0000-0000-000000000012")


def test_pattern_candidate_and_threshold_replays_coexist(tmp_path: Path) -> None:
    engine = _database_engine(tmp_path)
    _persist_timeline(engine)
    timeline_service = _timeline_service(engine)
    try:
        first_patterns = _pattern_service(
            engine,
            timeline_service,
            version="temporal-patterns-v1",
            run_id=PATTERN_RUN_ID,
        ).build(
            source_timeline_run_id=TIMELINE_RUN_ID,
            comparison_boundary=BOUNDARY,
        )
        replayed_patterns = _pattern_service(
            engine,
            timeline_service,
            version="temporal-patterns-v1",
            run_id=PATTERN_REPLAY_RUN_ID,
        ).build(
            source_timeline_run_id=TIMELINE_RUN_ID,
            comparison_boundary=BOUNDARY,
        )
        changed_patterns = _pattern_service(
            engine,
            timeline_service,
            version="temporal-patterns-v2",
            run_id=PATTERN_V2_RUN_ID,
        ).build(
            source_timeline_run_id=TIMELINE_RUN_ID,
            comparison_boundary=BOUNDARY,
        )

        first_candidates = _candidate_service(
            engine,
            run_id=CANDIDATE_RUN_ID,
        ).build(source_pattern_run_id=PATTERN_RUN_ID)
        replayed_candidates = _candidate_service(
            engine,
            run_id=CANDIDATE_REPLAY_RUN_ID,
        ).build(source_pattern_run_id=PATTERN_REPLAY_RUN_ID)
        changed_candidates = _candidate_service(
            engine,
            run_id=CANDIDATE_V2_RUN_ID,
        ).build(source_pattern_run_id=PATTERN_V2_RUN_ID)

        low = _evaluation_service(
            engine,
            run_id=LOW_EVALUATION_RUN_ID,
            threshold=0.65,
        ).evaluate(source_candidate_run_id=CANDIDATE_RUN_ID)
        high = _evaluation_service(
            engine,
            run_id=HIGH_EVALUATION_RUN_ID,
            threshold=0.95,
        ).evaluate(source_candidate_run_id=CANDIDATE_RUN_ID)
        memory = RecordingMemoryStore()
        promoted = _promotion_service(
            engine,
            memory,
            run_id=PROMOTION_RUN_ID,
        ).promote(source_evaluation_run_id=LOW_EVALUATION_RUN_ID)
        replayed_promotion = _promotion_service(
            engine,
            memory,
            run_id=PROMOTION_REPLAY_RUN_ID,
        ).promote(source_evaluation_run_id=LOW_EVALUATION_RUN_ID)
        rejected_promotion = _promotion_service(
            engine,
            memory,
            run_id=REJECTED_PROMOTION_RUN_ID,
        ).promote(source_evaluation_run_id=HIGH_EVALUATION_RUN_ID)

        assert len(first_patterns.patterns) == 3
        assert all(pattern.evidence_count >= 2 for pattern in first_patterns.patterns)
        assert {pattern.id for pattern in replayed_patterns.patterns} == {
            pattern.id for pattern in first_patterns.patterns
        }
        assert {pattern.id for pattern in changed_patterns.patterns}.isdisjoint(
            pattern.id for pattern in first_patterns.patterns
        )

        assert len(first_candidates.candidates) == 1
        candidate = first_candidates.candidates[0]
        assert len(candidate.source_ids) == 3
        assert candidate.source_ids == replayed_candidates.candidates[0].source_ids
        assert candidate.id == replayed_candidates.candidates[0].id
        assert candidate.id != changed_candidates.candidates[0].id

        assert low.decisions[0].candidate_id == candidate.id
        assert low.decisions[0].status is CandidateDecisionStatus.ACCEPTED
        assert low.decisions[0].reason is None
        assert high.decisions[0].candidate_id == candidate.id
        assert high.decisions[0].status is CandidateDecisionStatus.REJECTED
        assert high.decisions[0].reason == "below_memory_threshold"
        assert len(promoted.memory_links) == 1
        memory_link = promoted.memory_links[0]
        assert memory_link.candidate_decision_id == low.decisions[0].id
        assert set(memory_link.provenance.pattern_ids) == set(candidate.source_ids)
        assert len(memory_link.provenance.event_ids) == 3
        assert len(memory_link.provenance.observation_ids) == 3
        assert replayed_promotion.memory_links == promoted.memory_links
        assert rejected_promotion.memory_links == ()
        assert memory.entries == (candidate.text,)

        with session_scope(engine) as database_session:
            assert PatternRepository(database_session).patterns_for_processing_run(
                PATTERN_REPLAY_RUN_ID
            )
            assert PatternCandidateRepository(
                database_session
            ).candidates_for_processing_run(CANDIDATE_REPLAY_RUN_ID)
            candidate_build = CandidateBuildRepository(
                database_session
            ).by_processing_run(CANDIDATE_RUN_ID)
            low_build = CandidateEvaluationBuildRepository(
                database_session
            ).by_processing_run(LOW_EVALUATION_RUN_ID)
            high_build = CandidateEvaluationBuildRepository(
                database_session
            ).by_processing_run(HIGH_EVALUATION_RUN_ID)
            assert candidate_build is not None
            assert candidate_build.scoring_weights["utility"] == 0.22
            assert low_build is not None
            assert high_build is not None
            assert low_build.acceptance_threshold == 0.65
            assert high_build.acceptance_threshold == 0.95
    finally:
        engine.dispose()


def _pattern_service(
    engine: Engine,
    timeline_service: ActivityTimelineService,
    *,
    version: str,
    run_id: UUID,
) -> PatternAnalysisService:
    return PatternAnalysisService(
        engine=engine,
        timeline_service=timeline_service,
        pattern_engine=TemporalPatternEngine(
            clock=FixedClock(NOW),
            processing_version=version,
        ),
        clock=FixedClock(NOW),
        identifiers=SequenceIdentifiers((run_id,)),
    )


def _candidate_service(engine: Engine, *, run_id: UUID) -> PatternCandidateService:
    return PatternCandidateService(
        engine=engine,
        producer=PatternCandidateProducer(clock=FixedClock(NOW)),
        clock=FixedClock(NOW),
        identifiers=SequenceIdentifiers((run_id,)),
    )


def _evaluation_service(
    engine: Engine,
    *,
    run_id: UUID,
    threshold: float,
) -> CandidateEvaluationService:
    return CandidateEvaluationService(
        engine=engine,
        worker=TransparentCandidateWorker(
            clock=FixedClock(NOW),
            acceptance_threshold=threshold,
        ),
        clock=FixedClock(NOW),
        identifiers=SequenceIdentifiers((run_id,)),
    )


def _promotion_service(
    engine: Engine,
    memory: RecordingMemoryStore,
    *,
    run_id: UUID,
) -> MemoryPromotionService:
    return MemoryPromotionService(
        engine=engine,
        memory_store=memory,
        clock=FixedClock(NOW),
        identifiers=SequenceIdentifiers((run_id,)),
    )


def _timeline_service(engine: Engine) -> ActivityTimelineService:
    return ActivityTimelineService(
        engine=engine,
        sessionizer=ModelActivitySessionizer(),
        builder=SessionizedModelEventBuilder(
            clock=FixedClock(NOW),
            processing_version="session-events-v1",
        ),
        clock=FixedClock(NOW),
        identifiers=SequenceIdentifiers(()),
    )


def _persist_timeline(engine: Engine) -> None:
    segments = (
        (1, "Atlas", START + timedelta(hours=9), timedelta(hours=1)),
        (2, "Atlas", START + timedelta(days=2, hours=10), timedelta(hours=2)),
        (3, "Atlas", START + timedelta(days=2, hours=13), timedelta(hours=2)),
        (4, "Weak", START + timedelta(days=1, hours=12), timedelta(minutes=5)),
    )
    run = ProcessingRun(
        id=TIMELINE_RUN_ID,
        pipeline="activity_timeline",
        version="session-events-v1",
        started_at=START,
    ).succeed(
        ended_at=NOW,
        input_count=len(segments),
        output_count=len(segments),
    )
    build = TimelineBuild(
        processing_run_id=TIMELINE_RUN_ID,
        processing_version="session-events-v1",
        window_start=START,
        window_end=END,
        session_gap_seconds=600,
        max_session_duration_seconds=7200,
    )
    with session_scope(engine) as database_session:
        repository = PipelineRepository(database_session)
        repository.save_processing_run(run)
        TimelineBuildRepository(database_session).save(build)
        for index, project, started_at, duration in segments:
            observation = _observation(index, started_at, duration)
            event = _event(index, project, observation, duration)
            repository.save_observation(observation)
            repository.save_event(event)
            database_session.add(
                EventProcessingRunModel(
                    event_id=str(event.id),
                    processing_run_id=str(TIMELINE_RUN_ID),
                )
            )


def _observation(index: int, started_at: datetime, duration: timedelta) -> Observation:
    return Observation(
        id=UUID(f"70000000-0000-0000-0000-{index:012d}"),
        idempotency_key=f"{index:x}" * 64,
        source_type=SourceType.SYNTHETIC,
        captured_at=started_at,
        started_at=started_at,
        ended_at=started_at + duration,
        app_name="Synthetic Editor",
        app_bundle_id="test.synthetic.editor",
        expires_at=started_at + timedelta(hours=48),
        created_at=started_at,
    )


def _event(
    index: int,
    project: str,
    observation: Observation,
    duration: timedelta,
) -> Event:
    assert observation.started_at is not None
    assert observation.ended_at is not None
    return Event(
        id=UUID(f"80000000-0000-0000-0000-{index:012d}"),
        idempotency_key=f"{index + 4:x}" * 64,
        lineage_key=f"{index + 8:x}" * 64,
        type=EventType.PROJECT_WORK,
        summary=f"Synthetic work segment {index} for {project}.",
        facts={"active_seconds": int(duration.total_seconds())},
        started_at=observation.started_at,
        ended_at=observation.ended_at,
        valid_from=observation.started_at,
        valid_until=observation.ended_at,
        epistemic_status=EpistemicStatus.INFERRED,
        confidence=0.9,
        sensitivity=Sensitivity.PERSONAL,
        projects=(project,),
        source_observation_ids=(observation.id,),
        processing_version="session-events-v1",
        created_at=observation.started_at,
        updated_at=observation.started_at,
    )


def _database_engine(tmp_path: Path) -> Engine:
    database_path = tmp_path / "contx.db"
    upgrade_database(database_path)
    return create_database_engine(database_path)
