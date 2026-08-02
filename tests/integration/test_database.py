"""SQLite migration and safety integration tests."""

import sqlite3
import stat
from pathlib import Path
from uuid import UUID

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
from contx.db.repositories import PipelineRepository
from contx.models import EventType


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
        "event_model_transformations",
        "event_processing_runs",
        "event_corrections",
        "timeline_builds",
        "memory_candidates",
        "candidate_events",
        "memory_links",
        "processing_runs",
        "collection_control",
        "exclusion_rules",
        "model_transformations",
        "model_transformation_observations",
        "model_transformation_runs",
    } <= tables


def test_v001_observations_gain_bounded_expiry_on_upgrade(tmp_path: Path) -> None:
    database_path = tmp_path / "contx.db"
    upgrade_database(database_path, revision="20260802_0001")
    connection = sqlite3.connect(database_path)
    try:
        connection.execute(
            """INSERT INTO observations (
                id, idempotency_key, source_type, captured_at, started_at,
                ended_at, app_name, app_bundle_id, window_title, artifact_path,
                content_hash, perceptual_hash, excluded, exclusion_reason,
                processing_status, expires_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "00000000-0000-0000-0000-000000000001",
                "a" * 64,
                "active_app",
                "2026-08-02T10:00:00.000000Z",
                "2026-08-02T10:00:00.000000Z",
                "2026-08-02T10:00:00.000000Z",
                "Editor",
                "com.example.editor",
                None,
                None,
                None,
                None,
                0,
                None,
                "processed",
                None,
                "2026-08-02T10:00:00.000000Z",
            ),
        )
        connection.commit()
    finally:
        connection.close()

    upgrade_database(database_path)

    connection = sqlite3.connect(database_path)
    try:
        row = connection.execute(
            "SELECT activity_state, expires_at FROM observations"
        ).fetchone()
        columns = {
            item[1]: item[3]
            for item in connection.execute("PRAGMA table_info(observations)")
        }
    finally:
        connection.close()
    assert row == ("active", "2026-08-04T10:00:00.000Z")
    assert columns["expires_at"] == 1


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


def test_existing_events_gain_stable_lineage_and_validity_on_upgrade(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "contx.db"
    upgrade_database(database_path, revision="20260802_0004")
    connection = sqlite3.connect(database_path)
    try:
        connection.execute(
            """INSERT INTO observations (
                id, idempotency_key, source_type, activity_state, captured_at,
                started_at, ended_at, app_name, app_bundle_id, window_title,
                artifact_path, content_hash, perceptual_hash, excluded,
                exclusion_reason, processing_status, expires_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "00000000-0000-0000-0000-000000000021",
                "a" * 64,
                "synthetic",
                "active",
                "2026-08-02T10:00:00.000000Z",
                "2026-08-02T10:00:00.000000Z",
                "2026-08-02T10:20:00.000000Z",
                "Synthetic Editor",
                "dev.contx.synthetic",
                None,
                None,
                None,
                None,
                0,
                None,
                "processed",
                "2026-08-04T10:00:00.000000Z",
                "2026-08-02T10:00:00.000000Z",
            ),
        )
        connection.execute(
            """INSERT INTO events (
                id, idempotency_key, type, summary, facts, started_at, ended_at,
                epistemic_status, confidence, sensitivity, projects, entities,
                source_observation_ids, processing_version, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "00000000-0000-0000-0000-000000000022",
                "b" * 64,
                "unrecognized_legacy_activity",
                "Existing synthetic event.",
                "{}",
                "2026-08-02T10:00:00.000000Z",
                "2026-08-02T10:20:00.000000Z",
                "inferred",
                0.8,
                "personal",
                '["CONTX"]',
                "[]",
                '["00000000-0000-0000-0000-000000000021"]',
                "event-v1",
                "2026-08-02T10:21:00.000000Z",
                "2026-08-02T10:21:00.000000Z",
            ),
        )
        connection.commit()
    finally:
        connection.close()

    upgrade_database(database_path)

    connection = sqlite3.connect(database_path)
    try:
        row = connection.execute(
            "SELECT lineage_key, valid_from, valid_until FROM events"
        ).fetchone()
    finally:
        connection.close()
    assert row == (
        "b" * 64,
        "2026-08-02T10:00:00.000000Z",
        "2026-08-02T10:20:00.000000Z",
    )
    engine = create_database_engine(database_path)
    try:
        with session_scope(engine) as session:
            event = PipelineRepository(session).event_by_id(
                UUID("00000000-0000-0000-0000-000000000022")
            )
            assert event is not None
            assert event.type is EventType.OTHER
    finally:
        engine.dispose()


def test_existing_candidates_gain_explicit_scoring_components_on_upgrade(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "contx.db"
    upgrade_database(database_path, revision="20260802_0005")
    connection = sqlite3.connect(database_path)
    try:
        connection.execute(
            """INSERT INTO memory_candidates (
                id, idempotency_key, text, source_type, source_ids,
                importance, durability, novelty, confidence, sensitivity,
                score, status, rejection_reason, created_at, processed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "00000000-0000-0000-0000-000000000031",
                "c" * 64,
                "Existing synthetic candidate.",
                "event",
                '["00000000-0000-0000-0000-000000000032"]',
                0.8,
                0.7,
                0.6,
                0.75,
                "personal",
                0.72,
                "pending",
                None,
                "2026-08-02T10:00:00.000000Z",
                None,
            ),
        )
        connection.commit()
    finally:
        connection.close()

    upgrade_database(database_path)

    connection = sqlite3.connect(database_path)
    try:
        row = connection.execute(
            """SELECT utility, recurrence, ambiguity, redundancy, scoring_version
            FROM memory_candidates"""
        ).fetchone()
    finally:
        connection.close()
    assert row == (0.8, 0.0, 0.25, 0.0, "legacy-candidate-v1")


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
