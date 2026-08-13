"""Content-free pilot resource sampling tests."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest

import contx.evaluation.resources as resource_module
from contx.daemon import DaemonLeaseStatus
from contx.evaluation import (
    PilotEvaluationError,
    PilotManifest,
    PilotResourceSampler,
    PilotWorkspace,
    ProcessResourceMetrics,
    ResourcePhase,
    directory_usage_bytes,
    probe_process_resources,
)
from contx.settings import RuntimePaths
from tests.helpers import FixedClock

NOW = datetime(2026, 8, 3, 8, tzinfo=UTC)


def test_sampler_appends_process_and_disk_metrics_without_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace(tmp_path)
    paths = _runtime_paths(tmp_path)
    paths.raw.mkdir(parents=True)
    paths.application_support.mkdir(parents=True)
    (paths.raw / "artifact.bin").write_bytes(b"raw!")
    (paths.application_support / "contx.db").write_bytes(b"db!")
    monkeypatch.setattr(
        resource_module,
        "probe_daemon_lease",
        lambda _path: DaemonLeaseStatus(running=True, pid=42),
    )
    measured = {
        42: ProcessResourceMetrics(cpu_percent=1.0, rss_bytes=100),
        43: ProcessResourceMetrics(cpu_percent=2.0, rss_bytes=200),
    }

    sample = PilotResourceSampler(
        workspace=workspace,
        paths=paths,
        clock=FixedClock(NOW),
        process_probe=measured.__getitem__,
    ).capture(phase=ResourcePhase.MODEL_PROCESSING, local_model_pid=43)

    assert sample.total_cpu_percent == 3.0
    assert sample.total_rss_bytes == 300
    assert sample.raw_disk_bytes == 4
    assert sample.durable_disk_bytes == 3
    assert workspace.load().resource_samples == (sample,)


def test_sampler_requires_a_locked_running_collector(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        resource_module,
        "probe_daemon_lease",
        lambda _path: DaemonLeaseStatus(running=False),
    )

    with pytest.raises(PilotEvaluationError, match="collector to be running"):
        PilotResourceSampler(
            workspace=_workspace(tmp_path),
            paths=_runtime_paths(tmp_path),
            clock=FixedClock(NOW),
            process_probe=lambda _pid: ProcessResourceMetrics(1, 100),
        ).capture(phase=ResourcePhase.IDLE)


def test_sampler_rejects_double_counting_one_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        resource_module,
        "probe_daemon_lease",
        lambda _path: DaemonLeaseStatus(running=True, pid=42),
    )

    with pytest.raises(PilotEvaluationError, match="must be distinct"):
        PilotResourceSampler(
            workspace=_workspace(tmp_path),
            paths=_runtime_paths(tmp_path),
            clock=FixedClock(NOW),
            process_probe=lambda _pid: ProcessResourceMetrics(1, 100),
        ).capture(
            phase=ResourcePhase.MODEL_PROCESSING,
            local_model_pid=42,
        )


def test_active_sample_requires_a_measured_detection_latency(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        resource_module,
        "probe_daemon_lease",
        lambda _path: DaemonLeaseStatus(running=True, pid=42),
    )

    with pytest.raises(ValueError, match="require measured detection"):
        PilotResourceSampler(
            workspace=_workspace(tmp_path),
            paths=_runtime_paths(tmp_path),
            clock=FixedClock(NOW),
            process_probe=lambda _pid: ProcessResourceMetrics(1, 100),
        ).capture(phase=ResourcePhase.ACTIVE_COLLECTION)


def test_directory_usage_refuses_symlinked_entries(tmp_path: Path) -> None:
    root = tmp_path / "data"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.write_bytes(b"private")
    (root / "link").symlink_to(outside)

    with pytest.raises(PilotEvaluationError, match="contains a symlink"):
        directory_usage_bytes(root)


def test_native_probe_calculates_cpu_and_reads_rss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshots = iter(
        (
            resource_module._ProcessSnapshot(cpu_ticks=1_000, rss_bytes=1024),
            resource_module._ProcessSnapshot(cpu_ticks=3_000, rss_bytes=2048),
        )
    )
    monotonic = iter((1_000_000_000, 2_000_000_000))
    monkeypatch.setattr(
        resource_module, "_read_process_snapshot", lambda _pid: next(snapshots)
    )
    monkeypatch.setattr(resource_module, "_mach_timebase", lambda: (1, 1))
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(time, "monotonic_ns", lambda: next(monotonic))

    result = probe_process_resources(42)

    assert result.rss_bytes == 2048
    assert result.cpu_percent == pytest.approx(0.0002)


def _workspace(tmp_path: Path) -> PilotWorkspace:
    workspace = PilotWorkspace((tmp_path / "pilot").resolve())
    workspace.prepare(
        PilotManifest(
            pilot_id=UUID(int=1),
            started_at=NOW,
            planned_end_at=NOW + timedelta(days=7),
            timezone="Europe/Paris",
            target_machine="Synthetic Mac",
        )
    )
    return workspace


def _runtime_paths(tmp_path: Path) -> RuntimePaths:
    root = (tmp_path / "runtime").resolve()
    processing = root / "caches" / "processing"
    processing.mkdir(parents=True, exist_ok=True)
    return RuntimePaths(
        application_support=root / "application-support",
        caches=root / "caches",
        logs=root / "logs",
    )
