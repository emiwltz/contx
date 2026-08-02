"""Durable collection control and exclusion repository tests."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from sqlalchemy import Engine

from contx.collection import CollectionControlService
from contx.db import create_database_engine, upgrade_database
from contx.models import ExclusionRuleType

NOW = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)


class MutableClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def now(self) -> datetime:
        return self.value


def test_defaults_and_pause_state_survive_service_restart(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    clock = MutableClock(NOW)
    try:
        first = CollectionControlService(engine=engine, clock=clock)
        first.initialize()
        first.pause(duration=timedelta(minutes=15))

        restarted = CollectionControlService(engine=engine, clock=clock)

        assert restarted.control().is_paused(at=NOW)
        assert any(
            rule.pattern == "com.1password.1password"
            for rule in restarted.rules(enabled_only=True)
        )

        clock.value = NOW + timedelta(minutes=15)
        assert not restarted.control().is_paused(at=clock.value)
    finally:
        engine.dispose()


def test_user_rule_can_be_disabled_and_deleted(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    clock = MutableClock(NOW)
    rule_id = UUID("00000000-0000-0000-0000-000000000001")
    try:
        service = CollectionControlService(engine=engine, clock=clock)
        service.initialize()
        created = service.add_rule(
            rule_id=rule_id,
            rule_type=ExclusionRuleType.APP_BUNDLE_ID,
            pattern="com.example.private",
        )
        disabled = service.set_rule_enabled(created.id, enabled=False)
        service.delete_rule(created.id)

        assert not disabled.enabled
        assert all(rule.id != rule_id for rule in service.rules())
    finally:
        engine.dispose()


def _engine(tmp_path: Path) -> Engine:
    path = tmp_path / "contx.db"
    upgrade_database(path)
    return create_database_engine(path)
