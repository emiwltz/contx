"""Application service for durable collection controls and exclusions."""

from __future__ import annotations

from datetime import timedelta
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import ValidationError
from sqlalchemy import Engine

from contx.db import session_scope
from contx.db.repositories import CollectionRepository
from contx.errors import ConfigurationError
from contx.models import (
    Clock,
    CollectionControl,
    ExclusionRule,
    ExclusionRuleType,
)

_BUILT_IN_RULES: tuple[tuple[ExclusionRuleType, str], ...] = (
    (ExclusionRuleType.APP_BUNDLE_ID, "com.1password.1password"),
    (ExclusionRuleType.APP_BUNDLE_ID, "com.agilebits.onepassword7"),
    (ExclusionRuleType.APP_BUNDLE_ID, "com.apple.keychainaccess"),
    (ExclusionRuleType.APP_BUNDLE_ID, "com.bitwarden.desktop"),
    (ExclusionRuleType.APP_BUNDLE_ID, "com.dashlane.Dashlane"),
    (ExclusionRuleType.APP_BUNDLE_ID, "com.lastpass.LastPass"),
    (ExclusionRuleType.APP_BUNDLE_ID, "org.keepassxc.keepassxc"),
    (ExclusionRuleType.APP_NAME_CONTAINS, "1password"),
    (ExclusionRuleType.APP_NAME_CONTAINS, "bitwarden"),
    (ExclusionRuleType.APP_NAME_CONTAINS, "dashlane"),
    (ExclusionRuleType.APP_NAME_CONTAINS, "keepass"),
    (ExclusionRuleType.APP_NAME_CONTAINS, "lastpass"),
    (ExclusionRuleType.APP_NAME_CONTAINS, "password"),
    (ExclusionRuleType.APP_NAME_CONTAINS, "banking"),
    (ExclusionRuleType.APP_NAME_CONTAINS, "banque"),
    (ExclusionRuleType.WINDOW_TITLE_CONTAINS, "private browsing"),
    (ExclusionRuleType.WINDOW_TITLE_CONTAINS, "incognito"),
    (ExclusionRuleType.WINDOW_TITLE_CONTAINS, "inprivate"),
    (ExclusionRuleType.WINDOW_TITLE_CONTAINS, "navigation privée"),
    (ExclusionRuleType.WINDOW_TITLE_CONTAINS, "checkout"),
    (ExclusionRuleType.WINDOW_TITLE_CONTAINS, "payment"),
    (ExclusionRuleType.WINDOW_TITLE_CONTAINS, "paiement"),
    (ExclusionRuleType.WINDOW_TITLE_CONTAINS, "sign in"),
    (ExclusionRuleType.WINDOW_TITLE_CONTAINS, "log in"),
    (ExclusionRuleType.WINDOW_TITLE_CONTAINS, "connexion"),
    (ExclusionRuleType.SITUATION, "locked"),
    (ExclusionRuleType.SITUATION, "asleep"),
)


class CollectionControlService:
    """Provide one truthful state shared by CLI, daemon, and future UI."""

    def __init__(self, *, engine: Engine, clock: Clock) -> None:
        self._engine = engine
        self._clock = clock

    def initialize(self) -> None:
        now = self._clock.now()
        with session_scope(self._engine) as session:
            repository = CollectionRepository(session)
            repository.get_control(default_at=now)
            for rule_type, pattern in _BUILT_IN_RULES:
                repository.save_exclusion_rule(
                    ExclusionRule(
                        id=_built_in_identifier(rule_type, pattern),
                        rule_type=rule_type,
                        pattern=pattern,
                        built_in=True,
                        created_at=now,
                        updated_at=now,
                    )
                )

    def control(self) -> CollectionControl:
        now = self._clock.now()
        with session_scope(self._engine) as session:
            repository = CollectionRepository(session)
            control = repository.get_control(default_at=now)
            if control.paused_at is not None and not control.is_paused(at=now):
                control = repository.save_control(control.resume(at=now))
            return control

    def pause(self, *, duration: timedelta | None = None) -> CollectionControl:
        if duration is not None and not timedelta(0) < duration <= timedelta(days=30):
            raise ValueError("pause duration must be between zero and 30 days")
        now = self._clock.now()
        with session_scope(self._engine) as session:
            repository = CollectionRepository(session)
            current = repository.get_control(default_at=now)
            return repository.save_control(
                current.pause(
                    at=now,
                    until=None if duration is None else now + duration,
                )
            )

    def resume(self) -> CollectionControl:
        now = self._clock.now()
        with session_scope(self._engine) as session:
            repository = CollectionRepository(session)
            current = repository.get_control(default_at=now)
            return repository.save_control(current.resume(at=now))

    def rules(self, *, enabled_only: bool = False) -> tuple[ExclusionRule, ...]:
        with session_scope(self._engine) as session:
            return CollectionRepository(session).list_exclusion_rules(
                enabled_only=enabled_only
            )

    def add_rule(
        self, *, rule_id: UUID, rule_type: ExclusionRuleType, pattern: str
    ) -> ExclusionRule:
        now = self._clock.now()
        try:
            rule = ExclusionRule(
                id=rule_id,
                rule_type=rule_type,
                pattern=pattern,
                created_at=now,
                updated_at=now,
            )
        except ValidationError as error:
            raise ConfigurationError("Invalid exclusion rule") from error
        with session_scope(self._engine) as session:
            return CollectionRepository(session).save_exclusion_rule(rule)

    def set_rule_enabled(self, rule_id: UUID, *, enabled: bool) -> ExclusionRule:
        with session_scope(self._engine) as session:
            return CollectionRepository(session).set_exclusion_rule_enabled(
                rule_id,
                enabled=enabled,
                updated_at=self._clock.now(),
            )

    def delete_rule(self, rule_id: UUID) -> None:
        with session_scope(self._engine) as session:
            CollectionRepository(session).delete_exclusion_rule(rule_id)


def _built_in_identifier(rule_type: ExclusionRuleType, pattern: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"contx:exclusion:{rule_type.value}:{pattern}")
