"""Frozen-day session timeline, replay, and correction integration tests."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from sqlalchemy import Engine

from contx.application import (
    ActivityTimelineService,
    EventCorrectionService,
    correction_content,
)
from contx.db import create_database_engine, session_scope, upgrade_database
from contx.db.models import EventModel
from contx.db.repositories import (
    ModelEventRepository,
    ModelTransformationRepository,
    PipelineRepository,
    TimelineBuildRepository,
)
from contx.events import ModelActivitySessionizer, SessionizedModelEventBuilder
from contx.model_provider import (
    LocalModelExecution,
    LocalModelRuntimeStatus,
    ModelInterpretation,
    ModelTransformation,
)
from contx.models import (
    EpistemicStatus,
    EventCorrectionContent,
    EventType,
    Observation,
    ProcessingRun,
    Sensitivity,
    SourceType,
)
from tests.helpers import FixedClock, SequenceIdentifiers

DAY_START = datetime(2026, 8, 2, 0, 0, tzinfo=UTC)
DAY_END = DAY_START + timedelta(days=1)
MODEL_RUN_ID = UUID("00000000-0000-0000-0000-000000000901")
FIRST_TIMELINE_RUN_ID = UUID("00000000-0000-0000-0000-000000000902")
REPLAY_TIMELINE_RUN_ID = UUID("00000000-0000-0000-0000-000000000903")
V2_TIMELINE_RUN_ID = UUID("00000000-0000-0000-0000-000000000904")
CORRECTION_ID = UUID("00000000-0000-0000-0000-000000000905")
DIGEST = f"sha256:{'a' * 64}"


def test_frozen_day_build_is_intelligible_idempotent_and_version_comparable(
    tmp_path: Path,
) -> None:
    engine = _database_engine(tmp_path)
    transformations = _persist_frozen_evidence(engine)
    v1_service = _timeline_service(
        engine,
        processing_version="session-events-v1",
        identifiers=(FIRST_TIMELINE_RUN_ID, REPLAY_TIMELINE_RUN_ID),
    )
    v2_service = _timeline_service(
        engine,
        processing_version="session-events-v2",
        identifiers=(V2_TIMELINE_RUN_ID,),
    )
    try:
        first = v1_service.rebuild(window_start=DAY_START, window_end=DAY_END)
        replay = v1_service.rebuild(window_start=DAY_START, window_end=DAY_END)
        changed = v2_service.rebuild(window_start=DAY_START, window_end=DAY_END)

        assert first.succeeded
        assert first.transformation_count == 3
        assert len(first.timeline.entries) == 2
        project_entry, research_entry = first.timeline.entries
        assert project_entry.type is EventType.PROJECT_WORK
        assert project_entry.projects == ("CONTX",)
        assert project_entry.summary == (
            "Editing the CONTX timeline. Running CONTX timeline tests."
        )
        assert len(project_entry.source_observation_ids) == 2
        assert research_entry.type is EventType.RESEARCH
        assert research_entry.projects == ("OTHER",)

        assert replay.event_ids == first.event_ids
        assert replay.timeline.entries == first.timeline.entries
        assert changed.event_ids != first.event_ids
        assert tuple(entry.lineage_key for entry in changed.timeline.entries) == tuple(
            entry.lineage_key for entry in first.timeline.entries
        )

        with session_scope(engine) as session:
            pipeline = PipelineRepository(session)
            events = ModelEventRepository(session)
            assert pipeline.count(EventModel) == 4
            assert events.events_for_processing_run(FIRST_TIMELINE_RUN_ID)
            assert events.events_for_processing_run(REPLAY_TIMELINE_RUN_ID)
            assert events.events_for_processing_run(V2_TIMELINE_RUN_ID)
            first_event = events.events_for_processing_run(FIRST_TIMELINE_RUN_ID)[0]
            assert events.transformation_ids(first_event.id) == (
                transformations[0].id,
                transformations[1].id,
            )
            build = TimelineBuildRepository(session).by_processing_run(
                FIRST_TIMELINE_RUN_ID
            )
            assert build is not None
            assert build.window_start == DAY_START
            assert build.window_end == DAY_END
            assert build.session_gap_seconds == 600
            assert build.max_session_duration_seconds == 7200
    finally:
        engine.dispose()


def test_append_only_correction_survives_a_new_processing_version(
    tmp_path: Path,
) -> None:
    engine = _database_engine(tmp_path)
    _persist_frozen_evidence(engine)
    first_service = _timeline_service(
        engine,
        processing_version="session-events-v1",
        identifiers=(FIRST_TIMELINE_RUN_ID,),
    )
    changed_service = _timeline_service(
        engine,
        processing_version="session-events-v2",
        identifiers=(V2_TIMELINE_RUN_ID,),
    )
    try:
        first = first_service.rebuild(window_start=DAY_START, window_end=DAY_END)
        base_entry = first.timeline.entries[0]
        with session_scope(engine) as session:
            base_event = PipelineRepository(session).event_by_id(base_entry.event_id)
            assert base_event is not None
        replacement = EventCorrectionContent.model_validate(
            correction_content(base_event).model_dump()
            | {
                "summary": "Corrected CONTX implementation and test session.",
                "epistemic_status": EpistemicStatus.HYPOTHETICAL,
                "confidence": 0.6,
            }
        )
        correction_service = EventCorrectionService(engine=engine)
        first_correction = correction_service.correct(
            event_id=base_event.id,
            replacement=replacement,
            reason="The synthetic fixture intentionally corrects this session.",
            correction_id=CORRECTION_ID,
            created_at=DAY_START + timedelta(hours=12),
        )
        replayed_correction = correction_service.correct(
            event_id=base_event.id,
            replacement=replacement,
            reason="The synthetic fixture intentionally corrects this session.",
            correction_id=CORRECTION_ID,
            created_at=DAY_START + timedelta(hours=12),
        )

        changed = changed_service.rebuild(window_start=DAY_START, window_end=DAY_END)
        corrected_entry = changed.timeline.entries[0]

        assert replayed_correction == first_correction
        assert corrected_entry.event_id != base_event.id
        assert corrected_entry.lineage_key == base_event.lineage_key
        assert corrected_entry.correction_id == CORRECTION_ID
        assert (
            corrected_entry.summary
            == "Corrected CONTX implementation and test session."
        )
        assert corrected_entry.epistemic_status is EpistemicStatus.HYPOTHETICAL
        assert corrected_entry.confidence == 0.6
        assert corrected_entry.sensitivity is base_event.sensitivity
    finally:
        engine.dispose()


def _timeline_service(
    engine: Engine,
    *,
    processing_version: str,
    identifiers: tuple[UUID, ...],
) -> ActivityTimelineService:
    return ActivityTimelineService(
        engine=engine,
        sessionizer=ModelActivitySessionizer(),
        builder=SessionizedModelEventBuilder(
            clock=FixedClock(DAY_START + timedelta(hours=23)),
            processing_version=processing_version,
        ),
        clock=FixedClock(DAY_START + timedelta(hours=23)),
        identifiers=SequenceIdentifiers(identifiers),
    )


def _persist_frozen_evidence(engine: Engine) -> tuple[ModelTransformation, ...]:
    inputs = (
        (
            1,
            DAY_START + timedelta(hours=9),
            DAY_START + timedelta(hours=9, minutes=10),
            "coding",
            "CONTX",
            "Editing the CONTX timeline.",
            "com.example.editor",
        ),
        (
            2,
            DAY_START + timedelta(hours=9, minutes=15),
            DAY_START + timedelta(hours=9, minutes=25),
            "testing",
            "CONTX",
            "Running CONTX timeline tests.",
            "com.example.editor",
        ),
        (
            3,
            DAY_START + timedelta(hours=11),
            DAY_START + timedelta(hours=11, minutes=20),
            "research",
            "OTHER",
            "Researching another synthetic project.",
            "com.example.browser",
        ),
        (
            4,
            DAY_START + timedelta(days=1, hours=9),
            DAY_START + timedelta(days=1, hours=9, minutes=10),
            "coding",
            "CONTX",
            "Outside the selected day.",
            "com.example.editor",
        ),
    )
    records = tuple(_records(*values) for values in inputs)
    transformations = tuple(record[1] for record in records)
    run = ProcessingRun(
        id=MODEL_RUN_ID,
        pipeline="local_model",
        version="local-model-processing-v1",
        started_at=DAY_START + timedelta(hours=8),
    ).succeed(
        ended_at=DAY_START + timedelta(hours=22),
        input_count=len(records),
        output_count=len(records),
    )
    with session_scope(engine) as session:
        pipeline = PipelineRepository(session)
        pipeline.save_processing_run(run)
        repository = ModelTransformationRepository(session)
        for observation, succeeded, pending, running in records:
            pipeline.save_observation(observation)
            repository.save(pending)
            repository.save(running, processing_run_id=MODEL_RUN_ID)
            repository.save(succeeded, processing_run_id=MODEL_RUN_ID)
    return transformations


def _records(
    index: int,
    start: datetime,
    end: datetime,
    activity_type: str,
    project: str,
    summary: str,
    app_bundle_id: str,
) -> tuple[
    Observation,
    ModelTransformation,
    ModelTransformation,
    ModelTransformation,
]:
    observation_id = UUID(f"20000000-0000-0000-0000-{index:012d}")
    transformation_id = UUID(f"30000000-0000-0000-0000-{index:012d}")
    image_hash = f"{index + 8:x}" * 64
    observation = Observation(
        id=observation_id,
        idempotency_key=f"{index:x}" * 64,
        source_type=SourceType.SCREENSHOT,
        captured_at=start,
        started_at=start,
        ended_at=end,
        app_name="Synthetic Application",
        app_bundle_id=app_bundle_id,
        artifact_path=f"/synthetic/{index}.png",
        content_hash=image_hash,
        expires_at=start + timedelta(hours=48),
        created_at=start,
    )
    pending = ModelTransformation(
        id=transformation_id,
        idempotency_key=f"{index + 1:x}" * 64,
        source_observation_ids=(observation_id,),
        endpoint="http://127.0.0.1:11434",
        configured_model="synthetic-model",
        image_sha256=image_hash,
        next_attempt_at=start,
        created_at=start,
        updated_at=start,
    )
    runtime = LocalModelRuntimeStatus(
        endpoint="http://127.0.0.1:11434",
        runtime_available=True,
        runtime_version="0.32.5",
        model="synthetic-model",
        model_available=True,
        model_digest=DIGEST,
    )
    running = pending.start(runtime=runtime, started_at=start)
    execution = LocalModelExecution(
        request_id=transformation_id,
        source_observation_ids=(observation_id,),
        endpoint="http://127.0.0.1:11434",
        runtime_version="0.32.5",
        model="synthetic-model",
        model_digest=DIGEST,
        image_sha256=image_hash,
        interpretation=ModelInterpretation(
            summary=summary,
            activity_type=activity_type,
            observed_facts=(f"Synthetic activity segment {index} is visible.",),
            inferred_context=(f"Synthetic project context {index} is inferred.",),
            projects=(project,),
            entities=("CONTX",),
            sensitivity=Sensitivity.PERSONAL,
            sensitive_categories=(),
            confidence=0.8,
            memory_relevance=0.7,
        ),
        started_at=start,
        ended_at=end,
        wall_duration_ms=1000,
    )
    succeeded = running.succeed(execution)
    return observation, succeeded, pending, running


def _database_engine(tmp_path: Path) -> Engine:
    database_path = tmp_path / "contx.db"
    upgrade_database(database_path)
    return create_database_engine(database_path)
