"""Single-process daemon lease and truthful local status probing."""

from __future__ import annotations

import fcntl
import os
import stat
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from contx.errors import ConfigurationError, DaemonAlreadyRunningError

PRIVATE_FILE_MODE = 0o600


@dataclass(frozen=True, slots=True)
class DaemonLeaseStatus:
    """Live process-lock status; the PID is informational only."""

    running: bool
    pid: int | None = None


class DaemonLease:
    """Hold one exclusive advisory lock for a foreground collector process."""

    def __init__(self, path: Path) -> None:
        self._path = _validate_path(path)
        self._descriptor: int | None = None

    def acquire(self) -> None:
        if self._descriptor is not None:
            raise RuntimeError("daemon lease is already held by this object")
        descriptor = _open_lock_file(self._path, create=True)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            os.close(descriptor)
            raise DaemonAlreadyRunningError(
                "A CONTX collection daemon is already running"
            ) from error
        try:
            os.ftruncate(descriptor, 0)
            payload = f"{os.getpid()}\n".encode("ascii")
            if os.write(descriptor, payload) != len(payload):
                raise OSError("short daemon lease write")
            os.fsync(descriptor)
        except OSError as error:
            with suppress(OSError):
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)
            raise ConfigurationError("Cannot record the CONTX daemon lease") from error
        self._descriptor = descriptor

    def release(self) -> None:
        if self._descriptor is None:
            return
        descriptor = self._descriptor
        self._descriptor = None
        with suppress(OSError):
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)

    def __enter__(self) -> DaemonLease:
        self.acquire()
        return self

    def __exit__(self, *_error: object) -> None:
        self.release()


def probe_daemon_lease(path: Path) -> DaemonLeaseStatus:
    """Report a live lock holder instead of trusting stale configuration or PID."""
    safe_path = _validate_path(path)
    if not safe_path.exists():
        return DaemonLeaseStatus(running=False)
    descriptor = _open_lock_file(safe_path, create=False)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return DaemonLeaseStatus(running=True, pid=_read_pid(descriptor))
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        return DaemonLeaseStatus(running=False)
    finally:
        os.close(descriptor)


def _validate_path(path: Path) -> Path:
    if not path.is_absolute():
        raise ConfigurationError("The CONTX daemon lease path must be absolute")
    if path.is_symlink():
        raise ConfigurationError("The CONTX daemon lease must not be a symlink")
    if not path.parent.is_dir():
        raise ConfigurationError("The CONTX daemon lease directory does not exist")
    try:
        if path.parent.resolve(strict=True) != path.parent:
            raise ConfigurationError(
                "The CONTX daemon lease directory must not use symlinks"
            )
    except OSError as error:
        raise ConfigurationError(
            "Cannot inspect the CONTX daemon lease directory"
        ) from error
    return path


def _open_lock_file(path: Path, *, create: bool) -> int:
    flags = os.O_RDWR
    if create:
        flags |= os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags, PRIVATE_FILE_MODE)
        file_status = os.fstat(descriptor)
        if not stat.S_ISREG(file_status.st_mode):
            raise ConfigurationError("The CONTX daemon lease is not a regular file")
        if stat.S_IMODE(file_status.st_mode) != PRIVATE_FILE_MODE:
            if not create:
                raise ConfigurationError(
                    "The CONTX daemon lease permissions are too broad"
                )
            os.fchmod(descriptor, PRIVATE_FILE_MODE)
        return descriptor
    except ConfigurationError:
        if descriptor is not None:
            os.close(descriptor)
        raise
    except OSError as error:
        raise ConfigurationError("Cannot open the CONTX daemon lease") from error


def _read_pid(descriptor: int) -> int | None:
    try:
        os.lseek(descriptor, 0, os.SEEK_SET)
        raw_value = os.read(descriptor, 32).decode("ascii").strip()
        parsed = int(raw_value)
    except (OSError, UnicodeError, ValueError):
        return None
    return parsed if parsed > 0 else None
