"""Versioned loopback-only HTTP contract for the local interface."""

from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from fastapi.testclient import TestClient

from contx.application import (
    ActiveMemoryProjectionResult,
    ActiveMemoryProjectionService,
    ActiveMemoryWakeResult,
    DataDeletionService,
    InspectionService,
    RawPurgeService,
)
from contx.collection import CollectionControlService
from contx.db import create_database_engine, upgrade_database
from contx.memory_store import MemoryWake
from contx.model_provider import LocalModelRuntimeStatus
from contx.models import SystemClock, UuidIdentifierSource
from contx.raw_store import FilesystemRawStore
from contx.settings import AppSettings, RuntimePaths, initialize_runtime_paths
from contx.web import WebRuntime, create_app

NOW = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)


class UnavailableModelStatus:
    def status(self) -> LocalModelRuntimeStatus:
        return LocalModelRuntimeStatus(
            endpoint="http://127.0.0.1:11434",
            runtime_available=False,
            model="gemma4:e4b-it-qat",
            model_available=False,
            reason_code="runtime_unavailable",
        )


class EmptyWake:
    def wake(
        self, *, part: int = 1, snapshot: int | None = None
    ) -> ActiveMemoryWakeResult:
        return ActiveMemoryWakeResult(
            wake=MemoryWake(content="", complete=True, snapshot=42),
            projection=ActiveMemoryProjectionResult(
                fingerprint="a" * 64,
                generation="b" * 64,
                active_memory_count=0,
                rebuilt=False,
                completed_compressions=0,
            ),
        )


def test_api_reports_degraded_status_and_controls_persistent_collection(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    try:
        with TestClient(create_app(runtime)) as client:
            initial = client.get("/api/v1/status")
            paused = client.post("/api/v1/pause", json={"duration_minutes": 15})
            resumed = client.post("/api/v1/resume", json={})

        assert initial.status_code == 200
        assert initial.json()["health"] == "degraded"
        assert initial.json()["model"]["reason_code"] == "runtime_unavailable"
        assert not initial.json()["collection"]["paused"]
        assert paused.status_code == 200
        assert paused.json()["paused"]
        assert resumed.status_code == 200
        assert not resumed.json()["paused"]
    finally:
        runtime.engine.dispose()


def test_api_rejects_untrusted_hosts_cross_origin_and_non_json_mutations(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    try:
        with TestClient(create_app(runtime)) as client:
            hostile_host = client.get(
                "/api/v1/status", headers={"Host": "contx.attacker.example"}
            )
            cross_origin = client.post(
                "/api/v1/pause",
                json={},
                headers={"Origin": "https://attacker.example"},
            )
            form_post = client.post(
                "/api/v1/pause",
                content="duration_minutes=15",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )

        assert hostile_host.status_code == 400
        assert cross_origin.status_code == 403
        assert form_post.status_code == 415
        assert not runtime.controls.control().is_paused(at=SystemClock().now())
    finally:
        runtime.engine.dispose()


def test_exclusions_and_wake_use_versioned_application_contracts(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    try:
        with TestClient(create_app(runtime)) as client:
            created = client.post(
                "/api/v1/exclusions",
                json={
                    "rule_type": "app_bundle_id",
                    "pattern": "com.example.private",
                },
            )
            wake = client.get("/api/v1/memory/wake")
            guessed_raw_path = client.get(
                "/api/v1/privacy/raw-artifacts/00000000-0000-0000-0000-000000000001"
            )
            rule_id = created.json()["id"]
            deleted = client.request(
                "DELETE",
                f"/api/v1/exclusions/{rule_id}",
                json={},
            )

        assert created.status_code == 201
        assert created.json()["pattern"] == "com.example.private"
        assert wake.status_code == 200
        assert wake.json()["active_memory_count"] == 0
        assert "path" not in wake.json()
        assert guessed_raw_path.status_code == 404
        assert deleted.status_code == 204
    finally:
        runtime.engine.dispose()


def test_immediate_raw_purge_requires_exact_confirmation(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    try:
        with TestClient(create_app(runtime)) as client:
            rejected = client.request(
                "DELETE",
                "/api/v1/privacy/raw-artifacts",
                json={"confirmation": "delete"},
            )
            accepted = client.request(
                "DELETE",
                "/api/v1/privacy/raw-artifacts",
                json={"confirmation": "DELETE RAW ARTIFACTS"},
            )

        assert rejected.status_code == 422
        assert accepted.status_code == 200
        assert accepted.json()["status"] == "succeeded"
    finally:
        runtime.engine.dispose()


def test_full_deletion_requires_confirmation_and_retires_api(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    with TestClient(create_app(runtime)) as client:
        rejected = client.request(
            "DELETE",
            "/api/v1/system/data",
            json={"confirmation": "DELETE"},
        )
        accepted = client.request(
            "DELETE",
            "/api/v1/system/data",
            json={"confirmation": "DELETE ALL CONTX DATA"},
        )
        retired = client.get("/api/v1/status")

    assert rejected.status_code == 422
    assert accepted.status_code == 200
    assert accepted.json()["service_state"] == "data_deleted"
    assert retired.status_code == 410
    assert not runtime.paths.application_support.exists()


def _runtime(root: Path) -> WebRuntime:
    paths = RuntimePaths(
        application_support=root / "application-support",
        caches=root / "caches",
        logs=root / "logs",
    )
    initialize_runtime_paths(paths)
    upgrade_database(paths.database_file)
    engine = create_database_engine(paths.database_file)
    settings = AppSettings()
    clock = SystemClock()
    controls = CollectionControlService(engine=engine, clock=clock)
    controls.initialize()
    raw_store = FilesystemRawStore(paths.raw, disk_budget_bytes=1024 * 1024)
    inspection = InspectionService(
        engine=engine,
        paths=paths,
        settings=settings,
        raw_store=raw_store,
        model_provider=UnavailableModelStatus(),
        clock=clock,
    )
    return WebRuntime(
        engine=engine,
        paths=paths,
        settings=settings,
        clock=clock,
        controls=controls,
        inspection=inspection,
        active_memory=cast(ActiveMemoryProjectionService, EmptyWake()),
        raw_purge=RawPurgeService(
            engine=engine,
            raw_store=raw_store,
            clock=clock,
            identifiers=UuidIdentifierSource(),
        ),
        data_deletion=DataDeletionService(paths=paths, engine=engine),
        owns_engine=False,
    )
