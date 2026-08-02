"""Replayable local-model transformation lifecycle contracts."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from pydantic import ValidationError

from contx.model_provider import (
    LocalModelExecution,
    LocalModelRuntimeStatus,
    ModelInterpretation,
    ModelTransformation,
    ModelTransformationStatus,
)
from contx.models import Sensitivity

NOW = datetime(2026, 8, 2, 18, 0, tzinfo=UTC)
OBSERVATION_ID = UUID("00000000-0000-0000-0000-000000000001")
TRANSFORMATION_ID = UUID("00000000-0000-0000-0000-000000000002")
MODEL = "qwen3-vl:4b-instruct-q4_K_M"
DIGEST = f"sha256:{'a' * 64}"


def test_transformation_succeeds_only_with_exact_execution_provenance() -> None:
    pending = _pending()
    running = pending.start(runtime=_runtime(), started_at=NOW)

    succeeded = running.succeed(_execution())

    assert running.status is ModelTransformationStatus.RUNNING
    assert running.attempt_count == 1
    assert succeeded.status is ModelTransformationStatus.SUCCEEDED
    assert succeeded.interpretation == _interpretation()
    assert succeeded.model_digest == DIGEST
    assert succeeded.wall_duration_ms == 700
    assert succeeded.next_attempt_at is None

    mismatched = LocalModelExecution.model_validate(
        _execution().model_dump() | {"image_sha256": "b" * 64}
    )
    with pytest.raises(ValueError, match="provenance"):
        running.succeed(mismatched)


def test_failed_transformation_retries_only_when_due_with_same_model_digest() -> None:
    running = _pending().start(runtime=_runtime(), started_at=NOW)
    ended_at = NOW + timedelta(seconds=2)
    retry_at = NOW + timedelta(minutes=1)

    failed = running.fail(
        ended_at=ended_at,
        error_code="runtime_unavailable",
        next_attempt_at=retry_at,
    )

    assert failed.status is ModelTransformationStatus.FAILED
    assert failed.last_error_code == "runtime_unavailable"
    with pytest.raises(ValueError, match="retry is not due"):
        failed.start(runtime=_runtime(), started_at=retry_at - timedelta(seconds=1))
    with pytest.raises(ValueError, match="does not match"):
        failed.start(
            runtime=_runtime(model_digest=f"sha256:{'b' * 64}"),
            started_at=retry_at,
        )

    retried = failed.start(runtime=_runtime(), started_at=retry_at)

    assert retried.status is ModelTransformationStatus.RUNNING
    assert retried.attempt_count == 2
    assert retried.started_at == retry_at
    assert retried.last_error_code is None


def test_transformation_rejects_inconsistent_states_and_non_loopback_endpoint() -> None:
    with pytest.raises(ValidationError, match="pending model transformation"):
        ModelTransformation.model_validate(
            _pending().model_dump() | {"next_attempt_at": None}
        )

    with pytest.raises(ValidationError, match="literal loopback"):
        LocalModelRuntimeStatus(
            endpoint="http://ollama.internal:11434",
            runtime_available=True,
            runtime_version="0.32.5",
            model=MODEL,
            model_available=True,
            model_digest=DIGEST,
        )


def test_execution_start_must_match_the_persisted_attempt() -> None:
    running = _pending().start(runtime=_runtime(), started_at=NOW)
    execution = LocalModelExecution.model_validate(
        _execution().model_dump()
        | {
            "started_at": NOW + timedelta(milliseconds=1),
            "ended_at": NOW + timedelta(seconds=1),
        }
    )

    with pytest.raises(ValueError, match="provenance"):
        running.succeed(execution)


def _pending(**overrides: object) -> ModelTransformation:
    values: dict[str, object] = {
        "id": TRANSFORMATION_ID,
        "idempotency_key": "a" * 64,
        "source_observation_ids": (OBSERVATION_ID,),
        "endpoint": "http://127.0.0.1:11434",
        "configured_model": MODEL,
        "image_sha256": "c" * 64,
        "next_attempt_at": NOW,
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(overrides)
    return ModelTransformation.model_validate(values)


def _runtime(**overrides: object) -> LocalModelRuntimeStatus:
    values: dict[str, object] = {
        "endpoint": "http://127.0.0.1:11434",
        "runtime_available": True,
        "runtime_version": "0.32.5",
        "model": MODEL,
        "model_available": True,
        "model_digest": DIGEST,
    }
    values.update(overrides)
    return LocalModelRuntimeStatus.model_validate(values)


def _execution(**overrides: object) -> LocalModelExecution:
    values: dict[str, object] = {
        "request_id": TRANSFORMATION_ID,
        "source_observation_ids": (OBSERVATION_ID,),
        "endpoint": "http://127.0.0.1:11434",
        "runtime_version": "0.32.5",
        "model": MODEL,
        "model_digest": DIGEST,
        "image_sha256": "c" * 64,
        "interpretation": _interpretation(),
        "started_at": NOW,
        "ended_at": NOW + timedelta(seconds=1),
        "wall_duration_ms": 700,
        "runtime_duration_ms": 650,
        "load_duration_ms": 20,
        "prompt_eval_count": 100,
        "eval_count": 40,
    }
    values.update(overrides)
    return LocalModelExecution.model_validate(values)


def _interpretation() -> ModelInterpretation:
    return ModelInterpretation(
        summary="Editing CONTX persistence.",
        activity_type="coding",
        observed_facts=("A source file and tests are visible.",),
        inferred_context=("The CONTX project is being developed.",),
        projects=("CONTX",),
        entities=("Ollama",),
        sensitivity=Sensitivity.PERSONAL,
        sensitive_categories=(),
        confidence=0.9,
        memory_relevance=0.8,
    )
