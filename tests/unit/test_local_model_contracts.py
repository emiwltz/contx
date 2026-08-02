"""Strict local-model request and result contracts."""

import hashlib
from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from contx.model_provider import (
    LocalModelRequest,
    ModelInterpretation,
    SensitiveCategory,
)
from contx.models import ActivityState, Sensitivity

NOW = datetime(2026, 8, 2, 18, 0, tzinfo=UTC)
PNG = b"\x89PNG\r\n\x1a\nsynthetic-local-model-fixture"


def test_model_request_validates_content_hash_and_hides_private_input() -> None:
    request = _request()

    rendered = repr(request)
    dumped = request.model_dump()

    assert "private-window-title" not in rendered
    assert "synthetic-local-model-fixture" not in rendered
    assert "app_name" not in dumped
    assert "window_title" not in dumped
    assert "image_bytes" not in dumped
    assert request.prompt_metadata()["window_title"] == "private-window-title"


def test_model_request_rejects_hash_mismatch() -> None:
    with pytest.raises(ValidationError, match="hash does not match"):
        _request(image_sha256="0" * 64)


@pytest.mark.parametrize(
    "sensitivity",
    (Sensitivity.SENSITIVE, Sensitivity.FORBIDDEN),
)
def test_interpretation_requires_category_for_sensitive_content(
    sensitivity: Sensitivity,
) -> None:
    with pytest.raises(ValidationError, match="requires a sensitive category"):
        _interpretation(sensitivity=sensitivity, sensitive_categories=())


def test_interpretation_rejects_duplicate_provenance_labels() -> None:
    with pytest.raises(ValidationError, match="projects must be unique"):
        _interpretation(projects=("CONTX", "CONTX"))


@pytest.mark.parametrize("sensitivity", (Sensitivity.PUBLIC, Sensitivity.PERSONAL))
def test_sensitive_category_conservatively_raises_sensitivity_floor(
    sensitivity: Sensitivity,
) -> None:
    interpretation = _interpretation(
        sensitivity=sensitivity,
        sensitive_categories=(SensitiveCategory.CREDENTIAL,),
    )

    assert interpretation.sensitivity is Sensitivity.SENSITIVE


def test_government_identifier_is_a_supported_sensitive_category() -> None:
    interpretation = _interpretation(
        sensitivity=Sensitivity.SENSITIVE,
        sensitive_categories=(SensitiveCategory.GOVERNMENT_IDENTIFIER,),
    )

    assert interpretation.sensitive_categories == (
        SensitiveCategory.GOVERNMENT_IDENTIFIER,
    )


def test_interpretation_repr_hides_derived_private_text() -> None:
    interpretation = _interpretation(summary="private-derived-summary")

    assert "private-derived-summary" not in repr(interpretation)
    assert interpretation.model_dump()["summary"] == "private-derived-summary"


def _request(*, image_sha256: str | None = None) -> LocalModelRequest:
    return LocalModelRequest(
        id=UUID(int=1),
        source_observation_ids=(UUID(int=2),),
        captured_at=NOW,
        started_at=NOW,
        ended_at=NOW,
        activity_state=ActivityState.ACTIVE,
        app_name="Synthetic Editor",
        app_bundle_id="com.example.editor",
        window_title="private-window-title",
        image_sha256=image_sha256 or hashlib.sha256(PNG).hexdigest(),
        image_bytes=PNG,
    )


def _interpretation(**overrides: object) -> ModelInterpretation:
    values: dict[str, object] = {
        "summary": "Editing the CONTX local model boundary.",
        "activity_type": "coding",
        "observed_facts": ("A source file and tests are visible.",),
        "inferred_context": ("The user may be implementing CONTX.",),
        "projects": ("CONTX",),
        "entities": ("Ollama",),
        "sensitivity": Sensitivity.PERSONAL,
        "sensitive_categories": (),
        "confidence": 0.8,
        "memory_relevance": 0.7,
    }
    values.update(overrides)
    return ModelInterpretation.model_validate(values)
