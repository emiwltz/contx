"""Model transformation to event mapping contracts."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from contx.events import ModelTransformationEventBuilder
from contx.model_provider import (
    LocalModelExecution,
    LocalModelRuntimeStatus,
    ModelInterpretation,
    ModelTransformation,
)
from contx.models import (
    EpistemicStatus,
    Observation,
    ObservationStatus,
    Sensitivity,
    SourceType,
)
from tests.helpers import FixedClock

NOW = datetime(2026, 8, 2, 20, 0, tzinfo=UTC)
OBSERVATION_ID = UUID("00000000-0000-0000-0000-000000000601")
TRANSFORMATION_ID = UUID("00000000-0000-0000-0000-000000000602")
DIGEST = f"sha256:{'a' * 64}"


def test_successful_transformation_maps_to_deterministic_traceable_event() -> None:
    transformation = _succeeded()
    observation = _observation()
    builder = ModelTransformationEventBuilder(clock=FixedClock(NOW))

    first = builder.build(transformation, (observation,))
    replay = builder.build(transformation, (observation,))

    assert first == replay
    assert first.type == "coding"
    assert first.summary == "Editing the CONTX event pipeline."
    assert first.epistemic_status is EpistemicStatus.INFERRED
    assert first.projects == ("CONTX",)
    assert first.entities == ("Ollama",)
    assert first.source_observation_ids == (OBSERVATION_ID,)
    assert first.facts["memory_relevance"] == 0.8
    assert first.facts["model_transformation"] == {
        "id": str(TRANSFORMATION_ID),
        "provider": "ollama",
        "endpoint": "http://127.0.0.1:11434",
        "runtime_version": "0.32.5",
        "model": "qwen3-vl:4b-instruct-q4_K_M",
        "model_digest": DIGEST,
        "prompt_version": "local-screen-v2",
        "output_schema_version": "model-interpretation-v1",
        "image_sha256": "c" * 64,
    }


def test_builder_rejects_pending_mismatched_or_rejected_provenance() -> None:
    builder = ModelTransformationEventBuilder(clock=FixedClock(NOW))
    with pytest.raises(ValueError, match="successful"):
        builder.build(_pending(), (_observation(),))
    with pytest.raises(ValueError, match="do not match"):
        builder.build(
            _succeeded(),
            (_observation(id=UUID("00000000-0000-0000-0000-000000000699")),),
        )
    with pytest.raises(ValueError, match="unavailable"):
        builder.build(
            _succeeded(),
            (
                _observation(
                    processing_status=ObservationStatus.REJECTED,
                ),
            ),
        )


def test_purged_raw_observation_can_still_build_from_persisted_interpretation() -> None:
    purged = _observation(
        processing_status=ObservationStatus.PURGED,
        app_name=None,
        app_bundle_id=None,
        window_title=None,
        artifact_path=None,
        content_hash=None,
    )

    event = ModelTransformationEventBuilder(clock=FixedClock(NOW)).build(
        _succeeded(),
        (purged,),
    )

    assert event.source_observation_ids == (OBSERVATION_ID,)


def test_private_model_text_is_hidden_from_event_repr() -> None:
    event = ModelTransformationEventBuilder(clock=FixedClock(NOW)).build(
        _succeeded(),
        (_observation(),),
    )

    rendered = repr(event)

    assert "Editing the CONTX event pipeline" not in rendered
    assert "private-derived-context" not in rendered
    assert "CONTX" not in rendered


def _pending() -> ModelTransformation:
    return ModelTransformation(
        id=TRANSFORMATION_ID,
        idempotency_key="a" * 64,
        source_observation_ids=(OBSERVATION_ID,),
        endpoint="http://127.0.0.1:11434",
        configured_model="qwen3-vl:4b-instruct-q4_K_M",
        image_sha256="c" * 64,
        next_attempt_at=NOW,
        created_at=NOW,
        updated_at=NOW,
    )


def _succeeded() -> ModelTransformation:
    running = _pending().start(runtime=_runtime(), started_at=NOW)
    return running.succeed(
        LocalModelExecution(
            request_id=TRANSFORMATION_ID,
            source_observation_ids=(OBSERVATION_ID,),
            endpoint="http://127.0.0.1:11434",
            runtime_version="0.32.5",
            model="qwen3-vl:4b-instruct-q4_K_M",
            model_digest=DIGEST,
            image_sha256="c" * 64,
            interpretation=ModelInterpretation(
                summary="Editing the CONTX event pipeline.",
                activity_type="coding",
                observed_facts=("Source code is visible.",),
                inferred_context=("private-derived-context",),
                projects=("CONTX",),
                entities=("Ollama",),
                sensitivity=Sensitivity.PERSONAL,
                sensitive_categories=(),
                confidence=0.9,
                memory_relevance=0.8,
            ),
            started_at=NOW,
            ended_at=NOW + timedelta(seconds=1),
            wall_duration_ms=1000,
        )
    )


def _runtime() -> LocalModelRuntimeStatus:
    return LocalModelRuntimeStatus(
        endpoint="http://127.0.0.1:11434",
        runtime_available=True,
        runtime_version="0.32.5",
        model="qwen3-vl:4b-instruct-q4_K_M",
        model_available=True,
        model_digest=DIGEST,
    )


def _observation(**overrides: object) -> Observation:
    values: dict[str, object] = {
        "id": OBSERVATION_ID,
        "idempotency_key": "b" * 64,
        "source_type": SourceType.SCREENSHOT,
        "captured_at": NOW,
        "started_at": NOW,
        "ended_at": NOW + timedelta(seconds=1),
        "app_name": "Synthetic Editor",
        "app_bundle_id": "com.example.editor",
        "window_title": "CONTX event pipeline",
        "artifact_path": "/synthetic/screenshot.png",
        "content_hash": "c" * 64,
        "expires_at": NOW + timedelta(hours=48),
        "created_at": NOW,
    }
    values.update(overrides)
    return Observation.model_validate(values)
