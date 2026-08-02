"""Collection controls are enforced before sensitive work proceeds."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from sqlalchemy import Engine

from contx.collection import (
    CollectionControlService,
    CollectionPolicy,
    ControlledMetadataCollector,
)
from contx.db import create_database_engine, upgrade_database
from contx.models import Observation, SourceType
from tests.helpers import FixedClock, SequenceIdentifiers

NOW = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)
OBSERVATION_ID = UUID("00000000-0000-0000-0000-000000000001")


class PasswordManagerCollector:
    called = False

    def collect(self) -> tuple[Observation, ...]:
        self.called = True
        return (
            Observation(
                id=OBSERVATION_ID,
                idempotency_key="a" * 64,
                source_type=SourceType.ACTIVE_APP,
                captured_at=NOW,
                started_at=NOW,
                ended_at=NOW,
                app_name="1Password",
                app_bundle_id="com.1password.1password",
                window_title="Private vault",
                expires_at=NOW + timedelta(hours=48),
                created_at=NOW,
            ),
        )


class ForbiddenCollector:
    def collect(self) -> tuple[Observation, ...]:
        raise AssertionError("paused collection called the source collector")


def test_default_exclusion_drops_all_source_metadata(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    source = PasswordManagerCollector()
    try:
        collector = _controlled(engine, source, retain=False)

        assert collector.collect() == ()
        assert source.called
    finally:
        engine.dispose()


def test_optional_excluded_record_contains_no_app_or_title(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    try:
        (excluded,) = _controlled(
            engine, PasswordManagerCollector(), retain=True
        ).collect()

        assert excluded.source_type is SourceType.EXCLUDED_ACTIVITY
        assert excluded.excluded
        assert excluded.app_name is None
        assert excluded.app_bundle_id is None
        assert excluded.window_title is None
        assert excluded.artifact_path is None
        assert excluded.exclusion_reason is not None
        assert excluded.exclusion_reason.startswith("excluded_by_rule:")
    finally:
        engine.dispose()


def test_pause_short_circuits_the_source_collector(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    clock = FixedClock(NOW)
    controls = CollectionControlService(engine=engine, clock=clock)
    controls.initialize()
    controls.pause()
    try:
        collector = ControlledMetadataCollector(
            ForbiddenCollector(),
            controls=controls,
            policy=CollectionPolicy(),
            clock=clock,
            identifiers=SequenceIdentifiers((OBSERVATION_ID,)),
            retention=timedelta(hours=48),
            retain_excluded_activity=False,
        )

        assert collector.collect() == ()
    finally:
        engine.dispose()


def _controlled(
    engine: Engine,
    source: PasswordManagerCollector,
    *,
    retain: bool,
) -> ControlledMetadataCollector:
    clock = FixedClock(NOW)
    controls = CollectionControlService(engine=engine, clock=clock)
    controls.initialize()
    return ControlledMetadataCollector(
        source,
        controls=controls,
        policy=CollectionPolicy(),
        clock=clock,
        identifiers=SequenceIdentifiers((OBSERVATION_ID,)),
        retention=timedelta(hours=48),
        retain_excluded_activity=retain,
    )


def _engine(tmp_path: Path) -> Engine:
    path = tmp_path / "contx.db"
    upgrade_database(path)
    return create_database_engine(path)
