"""Single-process daemon lease safety and status tests."""

import os
import stat
from pathlib import Path

import pytest

from contx.daemon import DaemonLease, probe_daemon_lease
from contx.errors import ConfigurationError, DaemonAlreadyRunningError


def test_lease_reports_live_holder_and_releases_cleanly(tmp_path: Path) -> None:
    lock_path = tmp_path / "collector.lock"
    lease = DaemonLease(lock_path)

    lease.acquire()
    try:
        status = probe_daemon_lease(lock_path)

        assert status.running
        assert status.pid == os.getpid()
        assert stat.S_IMODE(lock_path.stat().st_mode) == 0o600
    finally:
        lease.release()

    assert not probe_daemon_lease(lock_path).running


def test_second_daemon_cannot_acquire_the_same_lease(tmp_path: Path) -> None:
    lock_path = tmp_path / "collector.lock"

    with (
        DaemonLease(lock_path),
        pytest.raises(DaemonAlreadyRunningError, match="already running"),
    ):
        DaemonLease(lock_path).acquire()


def test_process_lease_reports_its_configured_owner(tmp_path: Path) -> None:
    lock_path = tmp_path / "processor.lock"

    with (
        DaemonLease(lock_path, owner="context processor"),
        pytest.raises(
            DaemonAlreadyRunningError,
            match="CONTX context processor is already running",
        ),
    ):
        DaemonLease(lock_path, owner="context processor").acquire()


def test_symlink_lease_is_rejected_without_touching_target(tmp_path: Path) -> None:
    target = tmp_path / "outside"
    target.write_text("must remain", encoding="utf-8")
    link = tmp_path / "collector.lock"
    link.symlink_to(target)

    with pytest.raises(ConfigurationError, match="must not be a symlink"):
        DaemonLease(link)

    assert target.read_text(encoding="utf-8") == "must remain"


def test_status_rejects_broad_lock_permissions(tmp_path: Path) -> None:
    lock_path = tmp_path / "collector.lock"
    lock_path.write_text("123\n", encoding="ascii")
    lock_path.chmod(0o644)

    with pytest.raises(ConfigurationError, match="permissions are too broad"):
        probe_daemon_lease(lock_path)
