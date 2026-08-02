"""Bounded read models for the local interface."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from contx.application import InspectionService
from contx.db import create_database_engine, session_scope, upgrade_database
from contx.db.repositories import PipelineRepository
from contx.model_provider import LocalModelRuntimeStatus
from contx.models import ActivityState, Observation, SourceType
from contx.raw_store import FilesystemRawStore
from contx.settings import (
    AppSettings,
    RuntimePaths,
    initialize_runtime_paths,
)
from tests.helpers import FixedClock

NOW = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)
OBSERVATION_ID = UUID("00000000-0000-0000-0000-000000000101")


class UnavailableModelStatus:
    def status(self) -> LocalModelRuntimeStatus:
        return LocalModelRuntimeStatus(
            endpoint="http://127.0.0.1:11434",
            runtime_available=False,
            model="gemma4:e4b-it-qat",
            model_available=False,
            reason_code="runtime_unavailable",
        )


def test_overview_reports_real_control_storage_and_degraded_model(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    initialize_runtime_paths(paths)
    upgrade_database(paths.database_file)
    engine = create_database_engine(paths.database_file)
    raw_store = FilesystemRawStore(paths.raw, disk_budget_bytes=1024 * 1024)
    artifact = raw_store.write(
        b"synthetic screenshot bytes",
        artifact_id=OBSERVATION_ID,
        suffix=".png",
        captured_at=NOW,
        retention=timedelta(hours=48),
    )
    try:
        with session_scope(engine) as database_session:
            PipelineRepository(database_session).save_observation(
                Observation(
                    id=OBSERVATION_ID,
                    idempotency_key="a" * 64,
                    source_type=SourceType.SCREENSHOT,
                    activity_state=ActivityState.ACTIVE,
                    captured_at=NOW,
                    artifact_path=str(artifact.path),
                    content_hash=artifact.content_hash,
                    expires_at=NOW + timedelta(hours=48),
                    created_at=NOW,
                )
            )

        service = InspectionService(
            engine=engine,
            paths=paths,
            settings=AppSettings(),
            raw_store=raw_store,
            model_provider=UnavailableModelStatus(),
            clock=FixedClock(NOW),
        )
        overview = service.overview()
        privacy = service.privacy()

        assert not overview.collection.is_paused(at=NOW)
        assert overview.raw_usage_bytes == len(b"synthetic screenshot bytes")
        assert overview.counts["observations"] == 1
        assert overview.model_backlog == 1
        assert overview.issues[0].code == "runtime_unavailable"
        assert privacy.raw_artifacts[0].observation_id == OBSERVATION_ID
        assert privacy.raw_artifacts[0].available
        assert not hasattr(privacy.raw_artifacts[0], "path")
    finally:
        engine.dispose()


def _paths(root: Path) -> RuntimePaths:
    return RuntimePaths(
        application_support=root / "application-support",
        caches=root / "caches",
        logs=root / "logs",
    )
