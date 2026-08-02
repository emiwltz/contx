"""Synthetic screenshot selection, raw storage, replay, and purge integration."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from sqlalchemy import Engine

from contx.application import RawPurgeService
from contx.collection import (
    ActivitySample,
    CollectionPolicy,
    SelectiveScreenshotPlanner,
    SelectiveScreenshotService,
)
from contx.db import create_database_engine, session_scope, upgrade_database
from contx.db.repositories import PipelineRepository
from contx.models import (
    ActivityState,
    CollectionControl,
    ExclusionRule,
    ExclusionRuleType,
    ObservationStatus,
    SourceType,
)
from contx.raw_store import FilesystemRawStore
from tests.helpers import FixedClock, SequenceIdentifiers

NOW = datetime(2026, 8, 2, 16, 0, tzinfo=UTC)
SYNTHETIC_PNG = b"\x89PNG\r\n\x1a\nsynthetic-pixel-fixture"


class RecordingScreenshotSource:
    def __init__(self) -> None:
        self.calls = 0

    def capture_png(self) -> bytes:
        self.calls += 1
        return SYNTHETIC_PNG


def test_excluded_context_never_reads_synthetic_pixels(tmp_path: Path) -> None:
    source = RecordingScreenshotSource()
    service = _service(tmp_path, source)
    rule = ExclusionRule(
        id=UUID(int=1),
        rule_type=ExclusionRuleType.APP_BUNDLE_ID,
        pattern="com.example.private",
        created_at=NOW,
        updated_at=NOW,
    )

    result = service.consider(
        _sample(bundle="com.example.private"),
        control=CollectionControl(updated_at=NOW),
        rules=(rule,),
        manual_requested=True,
    )

    assert not result.decision.capture
    assert result.observation is None
    assert source.calls == 0
    assert not tuple((tmp_path / "raw").glob("*.png"))


def test_authorized_synthetic_capture_is_private_bounded_and_replay_safe(
    tmp_path: Path,
) -> None:
    source = RecordingScreenshotSource()
    service = _service(tmp_path, source)
    control = CollectionControl(updated_at=NOW)

    first = service.consider(
        _sample(),
        control=control,
        rules=(),
        manual_requested=True,
    )
    replay = service.consider(
        _sample(),
        control=control,
        rules=(),
        manual_requested=True,
    )

    assert first.observation is not None
    assert replay.observation == first.observation
    assert first.observation.source_type is SourceType.SCREENSHOT
    assert first.observation.expires_at == NOW + timedelta(hours=48)
    assert first.observation.artifact_path is not None
    artifact = Path(first.observation.artifact_path)
    assert artifact.read_bytes() == SYNTHETIC_PNG
    assert len(tuple((tmp_path / "raw").glob("*.png"))) == 1
    assert source.calls == 2


def test_later_identical_pixels_are_discarded_without_another_artifact(
    tmp_path: Path,
) -> None:
    source = RecordingScreenshotSource()
    service = _service(tmp_path, source)
    control = CollectionControl(updated_at=NOW)

    first = service.consider(
        _sample(),
        control=control,
        rules=(),
        manual_requested=True,
    )
    duplicate = service.consider(
        _sample(at=NOW + timedelta(seconds=20)),
        control=control,
        rules=(),
        manual_requested=True,
    )

    assert first.observation is not None
    assert duplicate.observation is None
    assert duplicate.discard_reason == "duplicate_content"
    assert source.calls == 2
    assert len(tuple((tmp_path / "raw").glob("*.png"))) == 1


def test_synthetic_capture_purges_through_observation_tombstone(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path)
    store = FilesystemRawStore(tmp_path / "raw", disk_budget_bytes=1024)
    service = _service(tmp_path, RecordingScreenshotSource(), store=store)
    result = service.consider(
        _sample(),
        control=CollectionControl(updated_at=NOW),
        rules=(),
    )
    assert result.observation is not None
    with session_scope(engine) as database_session:
        PipelineRepository(database_session).save_observation(result.observation)
    try:
        purged = RawPurgeService(
            engine=engine,
            raw_store=store,
            clock=FixedClock(NOW + timedelta(hours=49)),
            identifiers=SequenceIdentifiers((UUID(int=900),)),
        ).run()

        assert purged.succeeded
        assert purged.purged_observation_ids == (result.observation.id,)
        with session_scope(engine) as database_session:
            tombstone = PipelineRepository(database_session).save_observation(
                result.observation
            )
        assert tombstone.processing_status is ObservationStatus.PURGED
        assert tombstone.artifact_path is None
    finally:
        engine.dispose()


def _service(
    tmp_path: Path,
    source: RecordingScreenshotSource,
    *,
    store: FilesystemRawStore | None = None,
) -> SelectiveScreenshotService:
    return SelectiveScreenshotService(
        planner=SelectiveScreenshotPlanner(
            policy=CollectionPolicy(),
            enabled=True,
            minimum_interval=timedelta(seconds=15),
            maximum_interval=timedelta(seconds=120),
        ),
        source=source,
        raw_store=store or FilesystemRawStore(tmp_path / "raw", disk_budget_bytes=1024),
        retention=timedelta(hours=48),
    )


def _sample(
    *,
    bundle: str = "com.example.editor",
    at: datetime = NOW,
) -> ActivitySample:
    return ActivitySample(
        observed_at=at,
        activity_state=ActivityState.ACTIVE,
        app_name="Synthetic Editor",
        app_bundle_id=bundle,
    )


def _engine(tmp_path: Path) -> Engine:
    database = tmp_path / "contx.db"
    upgrade_database(database)
    return create_database_engine(database)
