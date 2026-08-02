"""Deterministic model-evidence sessionization and aggregation tests."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from contx.events import (
    ModelActivitySessionizer,
    ModelEventEvidence,
    SessionizedModelEventBuilder,
    normalize_event_type,
)
from contx.model_provider import (
    LocalModelExecution,
    LocalModelRuntimeStatus,
    ModelInterpretation,
    ModelTransformation,
)
from contx.models import (
    EpistemicStatus,
    EventType,
    Observation,
    Sensitivity,
    SourceType,
)
from tests.helpers import FixedClock

NOW = datetime(2026, 8, 2, 9, 0, tzinfo=UTC)
DIGEST = f"sha256:{'a' * 64}"


def test_sessionizer_is_order_independent_and_groups_continuous_project_work() -> None:
    coding = _evidence(
        1,
        start=NOW,
        end=NOW + timedelta(minutes=5),
        activity_type="coding",
        project="CONTX",
        app_bundle_id="com.example.editor",
    )
    research = _evidence(
        2,
        start=NOW + timedelta(minutes=8),
        end=NOW + timedelta(minutes=15),
        activity_type="research",
        project="CONTX",
        app_bundle_id="com.example.browser",
    )

    sessions = ModelActivitySessionizer().group((research, coding))

    assert len(sessions) == 1
    assert sessions[0].evidence == (coding, research)


def test_sessionizer_splits_long_gaps_different_projects_and_maximum_duration() -> None:
    first = _evidence(
        1,
        start=NOW,
        end=NOW + timedelta(minutes=5),
        activity_type="coding",
        project="CONTX",
    )
    another_project = _evidence(
        2,
        start=NOW + timedelta(minutes=6),
        end=NOW + timedelta(minutes=8),
        activity_type="coding",
        project="OTHER",
    )
    after_gap = _evidence(
        3,
        start=NOW + timedelta(minutes=30),
        end=NOW + timedelta(minutes=35),
        activity_type="coding",
        project="OTHER",
    )
    too_long = _evidence(
        4,
        start=NOW + timedelta(hours=2),
        end=NOW + timedelta(hours=2, minutes=5),
        activity_type="coding",
        project="OTHER",
    )

    sessions = ModelActivitySessionizer().group(
        (first, another_project, after_gap, too_long)
    )

    assert tuple(session.transformation_ids for session in sessions) == (
        (first.transformation.id,),
        (another_project.transformation.id,),
        (after_gap.transformation.id,),
        (too_long.transformation.id,),
    )


def test_session_builder_aggregates_semantics_conservatively_and_traceably() -> None:
    first = _evidence(
        1,
        start=NOW,
        end=NOW + timedelta(minutes=5),
        activity_type="coding",
        project="CONTX",
        sensitivity=Sensitivity.PUBLIC,
        confidence=0.9,
        summary="Editing the CONTX event model.",
    )
    second = _evidence(
        2,
        start=NOW + timedelta(minutes=8),
        end=NOW + timedelta(minutes=15),
        activity_type="testing",
        project="contx",
        sensitivity=Sensitivity.PERSONAL,
        confidence=0.7,
        summary="Running the synthetic timeline tests.",
        inferred_context=("The event model is under validation.",),
    )
    session = ModelActivitySessionizer().group((first, second))[0]

    event = SessionizedModelEventBuilder(clock=FixedClock(NOW)).build(session)

    assert event.type is EventType.PROJECT_WORK
    assert event.summary == (
        "Editing the CONTX event model. Running the synthetic timeline tests."
    )
    assert event.epistemic_status is EpistemicStatus.INFERRED
    assert event.sensitivity is Sensitivity.PERSONAL
    assert event.confidence == 0.7
    assert event.projects == ("CONTX",)
    assert event.source_observation_ids == (
        first.observations[0].id,
        second.observations[0].id,
    )
    assert event.valid_from == NOW
    assert event.valid_until == NOW + timedelta(minutes=15)
    assert event.facts["session"] == {
        "transformation_count": 2,
        "observation_count": 2,
    }
    assert event.facts["source_activity_types"] == ["coding", "testing"]


def test_changed_processing_version_replays_without_changing_event_lineage() -> None:
    evidence = _evidence(
        1,
        start=NOW,
        end=NOW + timedelta(minutes=5),
        activity_type="coding",
        project="CONTX",
    )
    session = ModelActivitySessionizer().group((evidence,))[0]

    first = SessionizedModelEventBuilder(
        clock=FixedClock(NOW),
        processing_version="session-events-v1",
    ).build(session)
    second = SessionizedModelEventBuilder(
        clock=FixedClock(NOW),
        processing_version="session-events-v2",
    ).build(session)

    assert first.lineage_key == second.lineage_key
    assert first.id != second.id
    assert first.idempotency_key != second.idempotency_key


def test_unknown_model_activity_maps_to_bounded_other_type() -> None:
    assert normalize_event_type("unexpected_new_label") is EventType.OTHER


@pytest.mark.parametrize(
    ("gap", "maximum"),
    (
        (timedelta(seconds=29), timedelta(hours=2)),
        (timedelta(minutes=10), timedelta(minutes=5)),
        (timedelta(minutes=10), timedelta(hours=5)),
    ),
)
def test_session_limits_are_bounded(gap: timedelta, maximum: timedelta) -> None:
    with pytest.raises(ValueError):
        ModelActivitySessionizer(
            session_gap=gap,
            max_session_duration=maximum,
        )


def _evidence(
    index: int,
    *,
    start: datetime,
    end: datetime,
    activity_type: str,
    project: str,
    app_bundle_id: str = "com.example.editor",
    sensitivity: Sensitivity = Sensitivity.PERSONAL,
    confidence: float = 0.9,
    summary: str = "Working on a synthetic project.",
    inferred_context: tuple[str, ...] = (),
) -> ModelEventEvidence:
    observation_id = UUID(f"00000000-0000-0000-0000-{index:012d}")
    transformation_id = UUID(f"10000000-0000-0000-0000-{index:012d}")
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
        content_hash=f"{index + 8:x}" * 64,
        expires_at=start + timedelta(hours=48),
        created_at=start,
    )
    pending = ModelTransformation(
        id=transformation_id,
        idempotency_key=f"{index + 1:x}" * 64,
        source_observation_ids=(observation_id,),
        endpoint="http://127.0.0.1:11434",
        configured_model="synthetic-model",
        image_sha256=observation.content_hash or "f" * 64,
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
        image_sha256=observation.content_hash or "f" * 64,
        interpretation=ModelInterpretation(
            summary=summary,
            activity_type=activity_type,
            observed_facts=(f"Synthetic fact {index} is visible.",),
            inferred_context=inferred_context,
            projects=(project,),
            entities=("CONTX",),
            sensitivity=sensitivity,
            sensitive_categories=(),
            confidence=confidence,
            memory_relevance=0.5,
        ),
        started_at=start,
        ended_at=end,
        wall_duration_ms=max(0, int((end - start).total_seconds() * 1000)),
    )
    return ModelEventEvidence(
        transformation=running.succeed(execution),
        observations=(observation,),
    )
