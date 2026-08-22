"""Durable collection control and exclusion repository tests."""

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import Engine

from contx.collection import CollectionControlService, read_collection_pause_state
from contx.db import create_database_engine, upgrade_database
from contx.errors import DatabaseError
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


def test_pause_state_preflight_read_does_not_normalize_expired_state(
    tmp_path: Path,
) -> None:
    path = tmp_path / "contx.db"
    upgrade_database(path)
    engine = create_database_engine(path)
    clock = MutableClock(NOW)
    try:
        service = CollectionControlService(engine=engine, clock=clock)
        service.initialize()
        service.pause(duration=timedelta(minutes=15))
    finally:
        engine.dispose()

    database_before = path.read_bytes()
    database_mtime_before = path.stat().st_mtime_ns
    assert read_collection_pause_state(path, at=NOW)
    assert not read_collection_pause_state(
        path,
        at=NOW + timedelta(minutes=15),
    )
    assert path.read_bytes() == database_before
    assert path.stat().st_mtime_ns == database_mtime_before
    connection = sqlite3.connect(path)
    try:
        row = connection.execute(
            "SELECT paused_at, pause_until FROM collection_control WHERE id = 1"
        ).fetchone()
    finally:
        connection.close()
    assert row is not None
    assert row[0] is not None
    assert row[1] is not None


def test_pause_state_preflight_refuses_a_missing_database(tmp_path: Path) -> None:
    with pytest.raises(DatabaseError, match="database is unavailable"):
        read_collection_pause_state(tmp_path / "missing.db", at=NOW)


def _engine(tmp_path: Path) -> Engine:
    path = tmp_path / "contx.db"
    upgrade_database(path)
    return create_database_engine(path)
