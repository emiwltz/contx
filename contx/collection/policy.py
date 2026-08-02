"""Collection pause and exclusion policy enforced before sensitive capture."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from contx.models import (
    ActivityState,
    CollectionControl,
    ExclusionRule,
    ExclusionRuleType,
)


@dataclass(frozen=True, slots=True)
class CollectionContext:
    """Only the metadata available before any screenshot or OCR."""

    activity_state: ActivityState
    app_name: str | None = None
    app_bundle_id: str | None = None
    window_title: str | None = None


@dataclass(frozen=True, slots=True)
class ExclusionDecision:
    excluded: bool
    reason_code: str | None = None
    rule_id: str | None = None


class CollectionPolicy:
    """Evaluate explicit controls without reading or capturing screen content."""

    def evaluate(
        self,
        context: CollectionContext,
        *,
        control: CollectionControl,
        rules: tuple[ExclusionRule, ...],
        at: datetime,
    ) -> ExclusionDecision:
        if control.is_paused(at=at):
            return ExclusionDecision(excluded=True, reason_code="collection_paused")
        for rule in rules:
            if rule.enabled and _matches(rule, context):
                return ExclusionDecision(
                    excluded=True,
                    reason_code="excluded_by_rule",
                    rule_id=str(rule.id),
                )
        return ExclusionDecision(excluded=False)


def _matches(rule: ExclusionRule, context: CollectionContext) -> bool:
    pattern = rule.pattern.casefold()
    if rule.rule_type is ExclusionRuleType.APP_BUNDLE_ID:
        return (
            context.app_bundle_id is not None
            and context.app_bundle_id.casefold() == pattern
        )
    if rule.rule_type is ExclusionRuleType.APP_NAME_CONTAINS:
        return context.app_name is not None and pattern in context.app_name.casefold()
    if rule.rule_type is ExclusionRuleType.WINDOW_TITLE_CONTAINS:
        return (
            context.window_title is not None
            and pattern in context.window_title.casefold()
        )
    if rule.rule_type is ExclusionRuleType.SITUATION:
        return context.activity_state.value.casefold() == pattern
    return False
