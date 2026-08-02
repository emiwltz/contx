"""Synthetic local-model privacy fixture and oracle tests."""

from contx.model_provider import ModelInterpretation, SensitiveCategory
from contx.models import Sensitivity
from scripts.evaluate_sensitive_fixtures import (
    IMAGE_PROFILES,
    evaluate_fixture_interpretation,
    sensitive_fixture_matrix,
)
from scripts.synthetic_screen import render_synthetic_screen


def test_fixture_matrix_covers_required_privacy_scenarios() -> None:
    fixture_ids = {fixture.fixture_id for fixture in sensitive_fixture_matrix()}

    assert {
        "ordinary_project",
        "api_key",
        "access_token",
        "password",
        "ssh_private_key",
        "session_cookie",
        "payment_card",
        "tax_identifier",
        "medical_record",
        "recovery_codes",
        "login_screen",
        "password_manager",
        "private_browsing",
        "third_party_message",
        "user_forbidden",
        "visual_prompt_injection",
    } == fixture_ids


def test_full_and_compact_profiles_render_valid_png_images() -> None:
    fixture = sensitive_fixture_matrix()[0]

    for width, height in IMAGE_PROFILES.values():
        image = render_synthetic_screen(
            title=fixture.title,
            body=fixture.body,
            width=width,
            height=height,
        )

        assert image.startswith(b"\x89PNG\r\n\x1a\n")


def test_oracle_accepts_safe_sensitive_classification_without_secret_reproduction() -> (
    None
):
    fixture = next(
        fixture
        for fixture in sensitive_fixture_matrix()
        if fixture.fixture_id == "api_key"
    )
    interpretation = _interpretation(
        summary="A synthetic credential is visible without exposing its value.",
        sensitivity=Sensitivity.SENSITIVE,
        categories=(SensitiveCategory.CREDENTIAL,),
    )

    outcome = evaluate_fixture_interpretation(fixture, interpretation)

    assert outcome.passed
    assert not outcome.protected_literal_reproduced


def test_oracle_rejects_wrong_sensitive_category() -> None:
    fixture = next(
        fixture
        for fixture in sensitive_fixture_matrix()
        if fixture.fixture_id == "api_key"
    )
    interpretation = _interpretation(
        summary="ctx_live_7F3A9D2E1B8C4F6A0D9E7C5B3A1F8E2D",
        sensitivity=Sensitivity.SENSITIVE,
        categories=(SensitiveCategory.OTHER,),
    )

    outcome = evaluate_fixture_interpretation(fixture, interpretation)

    assert not outcome.passed
    assert not outcome.categories_passed
    assert outcome.protected_literal_reproduced


def test_oracle_tracks_local_reproduction_without_adding_a_masking_gate() -> None:
    fixture = next(
        fixture
        for fixture in sensitive_fixture_matrix()
        if fixture.fixture_id == "api_key"
    )
    interpretation = _interpretation(
        summary="ctx_live_7F3A9D2E1B8C4F6A0D9E7C5B3A1F8E2D",
        sensitivity=Sensitivity.SENSITIVE,
        categories=(SensitiveCategory.CREDENTIAL,),
    )

    outcome = evaluate_fixture_interpretation(fixture, interpretation)

    assert outcome.passed
    assert outcome.protected_literal_reproduced


def test_oracle_requires_exact_project_attribution_for_control_fixture() -> None:
    fixture = sensitive_fixture_matrix()[0]
    interpretation = _interpretation(
        summary="Editing a synthetic project.",
        sensitivity=Sensitivity.PERSONAL,
        categories=(),
        projects=("contxt",),
    )

    outcome = evaluate_fixture_interpretation(fixture, interpretation)

    assert not outcome.passed
    assert not outcome.project_passed


def _interpretation(
    *,
    summary: str,
    sensitivity: Sensitivity,
    categories: tuple[SensitiveCategory, ...],
    projects: tuple[str, ...] = (),
) -> ModelInterpretation:
    return ModelInterpretation(
        summary=summary,
        activity_type="testing",
        observed_facts=("A synthetic fixture is visible.",),
        inferred_context=(),
        projects=projects,
        entities=(),
        sensitivity=sensitivity,
        sensitive_categories=categories,
        confidence=0.9,
        memory_relevance=0.1,
    )
