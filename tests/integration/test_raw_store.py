"""Atomic raw-artifact storage and bounded disk behavior."""

import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest

from contx.errors import RawStoreError, RawStoreFullError
from contx.raw_store import FilesystemRawStore

NOW = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)
ARTIFACT_ID = UUID("00000000-0000-0000-0000-000000000001")


def test_write_is_private_atomic_and_idempotent(tmp_path: Path) -> None:
    store = FilesystemRawStore(tmp_path / "raw", disk_budget_bytes=1024)

    first = store.write(
        b"synthetic-pixels",
        artifact_id=ARTIFACT_ID,
        suffix=".png",
        captured_at=NOW,
        retention=timedelta(hours=48),
    )
    replay = store.write(
        b"synthetic-pixels",
        artifact_id=ARTIFACT_ID,
        suffix=".png",
        captured_at=NOW,
        retention=timedelta(hours=48),
    )

    assert first == replay
    assert first.path.read_bytes() == b"synthetic-pixels"
    assert first.expires_at == NOW + timedelta(hours=48)
    assert store.list_paths() == (first.path,)
    assert stat.S_IMODE(store.root.stat().st_mode) == 0o700
    assert stat.S_IMODE(first.path.stat().st_mode) == 0o600
    assert not tuple(store.root.glob(".contx-raw-*.tmp"))


def test_budget_and_identity_conflicts_fail_before_overwrite(tmp_path: Path) -> None:
    store = FilesystemRawStore(tmp_path / "raw", disk_budget_bytes=5)
    store.write(
        b"12345",
        artifact_id=ARTIFACT_ID,
        suffix=".png",
        captured_at=NOW,
        retention=timedelta(hours=1),
    )

    with pytest.raises(RawStoreError, match="other content"):
        store.write(
            b"other",
            artifact_id=ARTIFACT_ID,
            suffix=".png",
            captured_at=NOW,
            retention=timedelta(hours=1),
        )
    with pytest.raises(RawStoreFullError, match="disk budget"):
        store.write(
            b"x",
            artifact_id=UUID("00000000-0000-0000-0000-000000000002"),
            suffix=".png",
            captured_at=NOW,
            retention=timedelta(hours=1),
        )
    assert (store.root / f"{ARTIFACT_ID}.png").read_bytes() == b"12345"


def test_interrupted_temp_is_removed_but_outside_paths_are_rejected(
    tmp_path: Path,
) -> None:
    store = FilesystemRawStore(tmp_path / "raw", disk_budget_bytes=1024)
    store.initialize()
    interrupted = store.root / ".contx-raw-interrupted.tmp"
    interrupted.write_bytes(b"partial-private-payload")

    store.initialize()

    assert not interrupted.exists()
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"must-remain")
    with pytest.raises(RawStoreError, match="outside"):
        store.delete(outside)
    assert outside.read_bytes() == b"must-remain"
