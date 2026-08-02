"""SQLite migration and safety integration tests."""

import sqlite3
import stat
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from contx.db import (
    create_database_engine,
    current_database_revision,
    head_database_revision,
    session_scope,
    upgrade_database,
)
from contx.db import models as persistence_models  # noqa: F401
from contx.db.base import Base


def test_empty_database_upgrades_to_packaged_head(tmp_path: Path) -> None:
    database_path = tmp_path / "Application Support" / "contx.db"

    revision = upgrade_database(database_path)

    assert revision == head_database_revision()
    assert current_database_revision(database_path) == revision
    assert stat.S_IMODE(database_path.stat().st_mode) == 0o600

    connection = sqlite3.connect(database_path)
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    finally:
        connection.close()
    assert {
        "observations",
        "events",
        "event_observations",
        "memory_candidates",
        "candidate_events",
        "memory_links",
        "processing_runs",
    } <= tables


def test_migration_matches_persistence_metadata(tmp_path: Path) -> None:
    database_path = tmp_path / "contx.db"
    upgrade_database(database_path)
    engine = create_database_engine(database_path)
    try:
        with engine.connect() as connection:
            differences = compare_metadata(
                MigrationContext.configure(connection), Base.metadata
            )
    finally:
        engine.dispose()

    assert differences == []


def test_empty_file_recovers_like_interrupted_initialization(tmp_path: Path) -> None:
    database_path = tmp_path / "contx.db"
    database_path.touch(mode=0o600)

    first_revision = upgrade_database(database_path)
    second_revision = upgrade_database(database_path)

    assert first_revision == second_revision == head_database_revision()


def test_sqlite_uses_wal_and_foreign_keys(tmp_path: Path) -> None:
    database_path = tmp_path / "contx.db"
    upgrade_database(database_path)
    engine = create_database_engine(database_path)
    try:
        with engine.connect() as connection:
            assert connection.execute(text("PRAGMA journal_mode")).scalar_one() == "wal"
            assert connection.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
    finally:
        engine.dispose()


def test_foreign_keys_reject_orphaned_provenance(tmp_path: Path) -> None:
    database_path = tmp_path / "contx.db"
    upgrade_database(database_path)
    engine = create_database_engine(database_path)
    try:
        with pytest.raises(IntegrityError), session_scope(engine) as session:
            session.execute(
                text(
                    """INSERT INTO event_observations (event_id, observation_id)
                    VALUES ('missing-event', 'missing-observation')"""
                )
            )
    finally:
        engine.dispose()
