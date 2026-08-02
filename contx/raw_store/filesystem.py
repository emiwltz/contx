"""Private, atomic, bounded filesystem storage for temporary raw artifacts."""

from __future__ import annotations

import fcntl
import hashlib
import os
import re
import stat
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from datetime import datetime, timedelta
from pathlib import Path
from uuid import UUID

from contx.errors import RawStoreError, RawStoreFullError
from contx.models.common import require_aware_utc
from contx.raw_store.base import RawArtifact

MAX_ARTIFACT_BYTES = 50 * 1024 * 1024
LOCK_FILE = ".contx-raw.lock"


class FilesystemRawStore:
    """Store unprocessed artifacts only inside one explicit cache root."""

    def __init__(self, root: Path, *, disk_budget_bytes: int) -> None:
        if not root.is_absolute():
            raise ValueError("raw-store root must be absolute")
        if disk_budget_bytes < 1:
            raise ValueError("raw-store disk budget must be positive")
        self._root = root
        self._disk_budget_bytes = disk_budget_bytes

    @property
    def root(self) -> Path:
        return self._root

    def initialize(self) -> None:
        try:
            self._root.mkdir(mode=0o700, parents=True, exist_ok=True)
            if self._root.is_symlink() or not self._root.is_dir():
                raise RawStoreError("Raw-store root is not a safe directory")
            self._root.chmod(0o700)
            with self._locked():
                self._cleanup_interrupted_writes()
        except OSError as error:
            raise RawStoreError("Cannot initialize private raw storage") from error

    def write(
        self,
        payload: bytes,
        *,
        artifact_id: UUID,
        suffix: str,
        captured_at: datetime,
        retention: timedelta,
    ) -> RawArtifact:
        self.initialize()
        captured = require_aware_utc(captured_at)
        if not timedelta(0) < retention <= timedelta(hours=48):
            raise RawStoreError("Raw retention must be between zero and 48 hours")
        if re.fullmatch(r"\.[a-z0-9]{1,8}", suffix) is None:
            raise RawStoreError("Raw artifact suffix is invalid")
        if len(payload) > MAX_ARTIFACT_BYTES:
            raise RawStoreFullError("Raw artifact exceeds the per-file safety limit")
        with self._locked():
            return self._write_locked(
                payload,
                artifact_id=artifact_id,
                suffix=suffix,
                captured_at=captured,
                retention=retention,
            )

    def _write_locked(
        self,
        payload: bytes,
        *,
        artifact_id: UUID,
        suffix: str,
        captured_at: datetime,
        retention: timedelta,
    ) -> RawArtifact:
        destination = self._root / f"{artifact_id}{suffix}"
        digest = hashlib.sha256(payload).hexdigest()
        existing = self._existing_artifact(
            destination,
            digest=digest,
            captured_at=captured_at,
            retention=retention,
        )
        if existing is not None:
            return existing
        if self._usage_bytes_unlocked() + len(payload) > self._disk_budget_bytes:
            raise RawStoreFullError("Raw storage disk budget would be exceeded")

        temporary: Path | None = None
        try:
            descriptor, name = tempfile.mkstemp(
                prefix=".contx-raw-", suffix=".tmp", dir=self._root
            )
            temporary = Path(name)
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb", closefd=True) as artifact_file:
                artifact_file.write(payload)
                artifact_file.flush()
                os.fsync(artifact_file.fileno())
            os.link(temporary, destination)
            destination.chmod(0o600)
            temporary.unlink()
            temporary = None
            _fsync_directory(self._root)
        except FileExistsError as error:
            existing = self._existing_artifact(
                destination,
                digest=digest,
                captured_at=captured_at,
                retention=retention,
            )
            if existing is None:
                raise RawStoreError(
                    "Raw artifact identity already has other content"
                ) from error
            return existing
        except OSError as error:
            raise RawStoreError("Cannot persist raw artifact") from error
        finally:
            if temporary is not None:
                with suppress(OSError):
                    temporary.unlink(missing_ok=True)
        return RawArtifact(
            path=destination,
            content_hash=digest,
            size_bytes=len(payload),
            captured_at=captured_at,
            expires_at=captured_at + retention,
        )

    def delete(self, path: Path) -> bool:
        self.initialize()
        candidate = self._validate_managed_path(path)
        try:
            with self._locked():
                if not candidate.exists():
                    return False
                if candidate.is_symlink() or not candidate.is_file():
                    raise RawStoreError("Raw artifact path is not a safe regular file")
                candidate.unlink()
                _fsync_directory(self._root)
                return True
        except RawStoreError:
            raise
        except OSError as error:
            raise RawStoreError("Cannot delete expired raw artifact") from error

    def size(self, path: Path) -> int:
        self.initialize()
        candidate = self._validate_managed_path(path)
        try:
            with self._locked():
                if not candidate.exists():
                    return 0
                file_status = candidate.stat(follow_symlinks=False)
                if not stat.S_ISREG(file_status.st_mode):
                    raise RawStoreError("Raw artifact path is not a safe regular file")
                return file_status.st_size
        except RawStoreError:
            raise
        except OSError as error:
            raise RawStoreError("Cannot inspect raw artifact") from error

    def usage_bytes(self) -> int:
        self.initialize()
        with self._locked():
            return self._usage_bytes_unlocked()

    def list_paths(self) -> tuple[Path, ...]:
        """List validated managed artifacts without exposing their contents."""
        self.initialize()
        with self._locked():
            paths: list[Path] = []
            try:
                with os.scandir(self._root) as entries:
                    for entry in entries:
                        if entry.name == LOCK_FILE:
                            continue
                        if entry.is_symlink() or not entry.is_file(
                            follow_symlinks=False
                        ):
                            raise RawStoreError("Raw storage contains an unsafe entry")
                        paths.append(Path(entry.path))
            except RawStoreError:
                raise
            except OSError as error:
                raise RawStoreError("Cannot list raw artifacts") from error
            return tuple(sorted(paths))

    def _usage_bytes_unlocked(self) -> int:
        total = 0
        try:
            with os.scandir(self._root) as entries:
                for entry in entries:
                    if entry.name == LOCK_FILE:
                        continue
                    if entry.is_symlink():
                        raise RawStoreError("Raw storage contains an unsafe link")
                    if entry.is_file(follow_symlinks=False):
                        total += entry.stat(follow_symlinks=False).st_size
        except RawStoreError:
            raise
        except OSError as error:
            raise RawStoreError("Cannot measure raw storage") from error
        return total

    def _existing_artifact(
        self,
        path: Path,
        *,
        digest: str,
        captured_at: datetime,
        retention: timedelta,
    ) -> RawArtifact | None:
        if not path.exists():
            return None
        if path.is_symlink() or not path.is_file():
            raise RawStoreError("Raw artifact identity is unsafe")
        try:
            content = path.read_bytes()
            file_status = path.stat()
        except OSError as error:
            raise RawStoreError("Cannot verify replayed raw artifact") from error
        if hashlib.sha256(content).hexdigest() != digest:
            raise RawStoreError("Raw artifact identity already has other content")
        return RawArtifact(
            path=path,
            content_hash=digest,
            size_bytes=file_status.st_size,
            captured_at=captured_at,
            expires_at=captured_at + retention,
        )

    def _validate_managed_path(self, path: Path) -> Path:
        if not path.is_absolute() or path.parent != self._root:
            raise RawStoreError("Raw artifact path is outside managed storage")
        if self._root.is_symlink() or not self._root.is_dir():
            raise RawStoreError("Raw-store root is not a safe directory")
        return path

    def _cleanup_interrupted_writes(self) -> None:
        removed = False
        with os.scandir(self._root) as entries:
            for entry in entries:
                if not entry.name.startswith(".contx-raw-") or not entry.name.endswith(
                    ".tmp"
                ):
                    continue
                if entry.is_symlink() or not entry.is_file(follow_symlinks=False):
                    raise RawStoreError("Raw storage contains an unsafe temp entry")
                Path(entry.path).unlink()
                removed = True
        if removed:
            _fsync_directory(self._root)

    @contextmanager
    def _locked(self) -> Iterator[None]:
        lock_path = self._root / LOCK_FILE
        try:
            descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        except OSError as error:
            raise RawStoreError("Cannot lock raw storage") from error
        with os.fdopen(descriptor, "a+b", closefd=True) as lock_file:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            except OSError as error:
                raise RawStoreError("Cannot lock raw storage") from error
            yield


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
