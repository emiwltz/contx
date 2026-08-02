"""Pre-capture pause and exclusion behavior."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from contx.collection import CollectionContext, CollectionPolicy
from contx.models import (
    ActivityState,
    CollectionControl,
    ExclusionRule,
    ExclusionRuleType,
)

NOW = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)


def test_pause_excludes_before_rules_are_inspected() -> None:
    control = CollectionControl(updated_at=NOW).pause(at=NOW)

    decision = CollectionPolicy().evaluate(
        CollectionContext(
            activity_state=ActivityState.ACTIVE,
            app_name="Synthetic Editor",
        ),
        control=control,
        rules=(),
        at=NOW,
    )

    assert decision.excluded
    assert decision.reason_code == "collection_paused"


def test_application_and_window_rules_are_case_insensitive() -> None:
    rules = (
        _rule(ExclusionRuleType.APP_BUNDLE_ID, "com.example.secret"),
        _rule(ExclusionRuleType.WINDOW_TITLE_CONTAINS, "payment"),
    )
    policy = CollectionPolicy()
    control = CollectionControl(updated_at=NOW)

    app = policy.evaluate(
        CollectionContext(
            activity_state=ActivityState.ACTIVE,
            app_bundle_id="COM.EXAMPLE.SECRET",
        ),
        control=control,
        rules=rules,
        at=NOW,
    )
    window = policy.evaluate(
        CollectionContext(
            activity_state=ActivityState.ACTIVE,
            window_title="Complete PAYMENT now",
        ),
        control=control,
        rules=rules,
        at=NOW,
    )

    assert app.excluded and app.rule_id is not None
    assert window.excluded and window.rule_id is not None


def test_disabled_rule_does_not_exclude() -> None:
    disabled = ExclusionRule.model_validate(
        _rule(ExclusionRuleType.APP_NAME_CONTAINS, "password").model_dump()
        | {"enabled": False}
    )

    decision = CollectionPolicy().evaluate(
        CollectionContext(
            activity_state=ActivityState.ACTIVE,
            app_name="Password Manager",
        ),
        control=CollectionControl(updated_at=NOW),
        rules=(disabled,),
        at=NOW,
    )

    assert not decision.excluded


def test_timed_pause_expires_at_the_boundary() -> None:
    control = CollectionControl(updated_at=NOW).pause(
        at=NOW, until=NOW + timedelta(minutes=15)
    )

    assert control.is_paused(at=NOW + timedelta(minutes=14, seconds=59))
    assert not control.is_paused(at=NOW + timedelta(minutes=15))


def _rule(rule_type: ExclusionRuleType, pattern: str) -> ExclusionRule:
    return ExclusionRule(
        id=UUID("00000000-0000-0000-0000-000000000001"),
        rule_type=rule_type,
        pattern=pattern,
        created_at=NOW,
        updated_at=NOW,
    )
