"""Fail-closed lifecycle for the two CONTX user LaunchAgents."""

from __future__ import annotations

import os
import stat
import subprocess
import time
from collections.abc import Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from contx.errors import ConfigurationError

PRIVATE_MANIFEST_MODE = 0o600
PRIVATE_DIRECTORY_MODE = 0o700
MAX_MANIFEST_BYTES = 64 * 1024
DEFAULT_LAUNCHCTL_TIMEOUT_SECONDS = 10.0
DEFAULT_STOP_TIMEOUT_SECONDS = 5.0
DEFAULT_STOP_POLL_INTERVAL_SECONDS = 0.1


class ManifestState(StrEnum):
    """Content-free ownership state for one expected plist."""

    MISSING = "missing"
    MATCHING = "matching"
    CONFLICTING = "conflicting"
    UNSAFE = "unsafe"


@dataclass(frozen=True, slots=True)
class LaunchAgentSpec:
    """One exact CONTX-owned user-agent manifest."""

    label: str
    path: Path
    manifest: bytes

    def __post_init__(self) -> None:
        if not self.label or any(character in self.label for character in "\x00/\r\n"):
            raise ValueError("LaunchAgent label is invalid")
        if not self.path.is_absolute() or self.path.name != f"{self.label}.plist":
            raise ValueError("LaunchAgent path must be absolute and label-derived")
        if not self.manifest or len(self.manifest) > MAX_MANIFEST_BYTES:
            raise ValueError("LaunchAgent manifest size is invalid")


@dataclass(frozen=True, slots=True)
class LaunchAgentStatus:
    """Inspectable state without launchctl or plist output content."""

    label: str
    manifest_state: ManifestState
    loaded: bool


class LaunchctlRunner(Protocol):
    """Content-free launchctl command boundary."""

    def run(self, arguments: Sequence[str]) -> int: ...


class SubprocessLaunchctlRunner:
    """Execute only explicit launchctl argv and discard native output."""

    def __init__(
        self,
        executable: Path = Path("/bin/launchctl"),
        *,
        timeout_seconds: float = DEFAULT_LAUNCHCTL_TIMEOUT_SECONDS,
    ) -> None:
        if not executable.is_absolute():
            raise ValueError("launchctl executable path must be absolute")
        if timeout_seconds <= 0:
            raise ValueError("launchctl timeout must be positive")
        self._executable = executable
        self._timeout_seconds = timeout_seconds

    def run(self, arguments: Sequence[str]) -> int:
        if not arguments or any(
            not argument or "\x00" in argument for argument in arguments
        ):
            raise ConfigurationError("launchctl arguments are invalid")
        _validate_launchctl_executable(self._executable)
        try:
            result = subprocess.run(
                (str(self._executable), *arguments),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=self._timeout_seconds,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ConfigurationError("launchctl could not complete safely") from error
        return result.returncode


class UserLaunchAgentLifecycle:
    """Stage, load, unload, and remove exact user LaunchAgents together."""

    def __init__(
        self,
        *,
        specs: tuple[LaunchAgentSpec, ...],
        launch_agents_directory: Path,
        user_id: int,
        runner: LaunchctlRunner | None = None,
        stop_timeout_seconds: float = DEFAULT_STOP_TIMEOUT_SECONDS,
        stop_poll_interval_seconds: float = DEFAULT_STOP_POLL_INTERVAL_SECONDS,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not specs or len({spec.label for spec in specs}) != len(specs):
            raise ValueError("LaunchAgent specs must have unique labels")
        if user_id < 0:
            raise ValueError("LaunchAgent user ID cannot be negative")
        if not launch_agents_directory.is_absolute():
            raise ValueError("LaunchAgent directory must be absolute")
        if any(spec.path.parent != launch_agents_directory for spec in specs):
            raise ValueError("LaunchAgent specs must share the managed directory")
        if stop_timeout_seconds <= 0 or stop_poll_interval_seconds <= 0:
            raise ValueError("LaunchAgent stop wait durations must be positive")
        self._specs = specs
        self._directory = launch_agents_directory
        self._domain = f"gui/{user_id}"
        self._runner = runner or SubprocessLaunchctlRunner()
        self._stop_timeout_seconds = stop_timeout_seconds
        self._stop_poll_interval_seconds = stop_poll_interval_seconds
        self._monotonic = monotonic
        self._sleep = sleep

    def inspect(self) -> tuple[LaunchAgentStatus, ...]:
        """Read exact manifest and loaded states without changing either."""
        return tuple(
            LaunchAgentStatus(
                label=spec.label,
                manifest_state=_manifest_state(spec),
                loaded=self._is_loaded(spec.label),
            )
            for spec in self._specs
        )

    def stage(self) -> tuple[str, ...]:
        """Create only missing exact manifests and never overwrite a file."""
        _ensure_managed_directory(self._directory)
        created: list[LaunchAgentSpec] = []
        try:
            for spec in self._specs:
                state = _manifest_state(spec)
                if state is ManifestState.MATCHING:
                    continue
                if state is not ManifestState.MISSING:
                    raise ConfigurationError(
                        f"LaunchAgent manifest is {state.value}: {spec.path.name}"
                    )
                _write_new_manifest(spec)
                created.append(spec)
        except BaseException as error:
            rollback_failures: list[BaseException] = []
            for spec in reversed(created):
                try:
                    _unlink_matching_manifest(spec)
                except BaseException as rollback_error:
                    rollback_failures.append(rollback_error)
            if rollback_failures:
                raise ConfigurationError(
                    "LaunchAgent staging failed and rollback is incomplete"
                ) from error
            raise
        return tuple(spec.label for spec in created)

    def load(self) -> tuple[str, ...]:
        """Load every staged job and roll back newly loaded jobs on failure."""
        statuses = self.inspect()
        _require_matching_manifests(statuses)
        missing = tuple(
            spec
            for spec, status in zip(self._specs, statuses, strict=True)
            if not status.loaded
        )
        if not missing:
            return ()
        try:
            return_code = self._runner.run(
                ("bootstrap", self._domain, *(str(spec.path) for spec in missing))
            )
        except BaseException as error:
            try:
                loaded_after_interrupt = {
                    spec.label for spec in missing if self._is_loaded(spec.label)
                }
                self._rollback_loaded(loaded_after_interrupt)
            except BaseException:
                raise ConfigurationError(
                    "CONTX LaunchAgent loading was interrupted and rollback "
                    "is incomplete"
                ) from error
            raise
        loaded_after = {spec.label for spec in missing if self._is_loaded(spec.label)}
        if return_code == 0 and loaded_after == {spec.label for spec in missing}:
            return tuple(spec.label for spec in missing)
        self._rollback_loaded(loaded_after)
        raise ConfigurationError("CONTX LaunchAgents could not be loaded together")

    def unload(self) -> tuple[str, ...]:
        """Stop only loaded jobs whose on-disk manifests still match exactly."""
        return self.unload_staged(tuple(spec.label for spec in self._specs))

    def unload_staged(self, labels: tuple[str, ...]) -> tuple[str, ...]:
        """Stop a selected exact subset, typically activation rollback."""
        requested = set(labels)
        known = {spec.label for spec in self._specs}
        if len(requested) != len(labels) or not requested <= known:
            raise ValueError("LaunchAgent unload labels are invalid")
        statuses = self.inspect()
        selected = tuple(status for status in statuses if status.label in requested)
        for status in selected:
            if status.loaded and status.manifest_state is not ManifestState.MATCHING:
                raise ConfigurationError(
                    f"Refusing to unload unmanaged LaunchAgent: {status.label}"
                )
        unloaded: list[str] = []
        for status in selected:
            if not status.loaded:
                continue
            return_code = self._runner.run(
                ("bootout", self._service_target(status.label))
            )
            if not self._wait_until_unloaded(status.label):
                raise ConfigurationError(
                    f"LaunchAgent did not stop: {status.label} (code {return_code})"
                )
            unloaded.append(status.label)
        return tuple(unloaded)

    def remove(self) -> tuple[str, ...]:
        """Delete only exact stopped manifests; modified files are never removed."""
        return self.remove_staged(tuple(spec.label for spec in self._specs))

    def remove_staged(self, labels: tuple[str, ...]) -> tuple[str, ...]:
        """Delete a selected exact stopped subset, typically activation rollback."""
        requested = set(labels)
        known = {spec.label for spec in self._specs}
        if len(requested) != len(labels) or not requested <= known:
            raise ValueError("LaunchAgent removal labels are invalid")
        statuses = self.inspect()
        selected_statuses = tuple(
            (spec, status)
            for spec, status in zip(self._specs, statuses, strict=True)
            if spec.label in requested
        )
        if any(status.loaded for _spec, status in selected_statuses):
            raise ConfigurationError("Refusing to remove a loaded LaunchAgent")
        changed = tuple(
            spec.path.name
            for spec, status in selected_statuses
            if status.manifest_state
            not in {ManifestState.MISSING, ManifestState.MATCHING}
        )
        if changed:
            raise ConfigurationError(
                f"Refusing to remove modified LaunchAgent: {', '.join(changed)}"
            )
        removed: list[str] = []
        for spec, status in selected_statuses:
            if status.manifest_state is ManifestState.MISSING:
                continue
            _unlink_matching_manifest(spec)
            removed.append(spec.label)
        return tuple(removed)

    def _is_loaded(self, label: str) -> bool:
        return self._runner.run(("print", self._service_target(label))) == 0

    def _wait_until_unloaded(self, label: str) -> bool:
        """Allow launchd's accepted bootout to finish before declaring failure."""
        deadline = self._monotonic() + self._stop_timeout_seconds
        while self._is_loaded(label):
            remaining = deadline - self._monotonic()
            if remaining <= 0:
                return False
            self._sleep(min(self._stop_poll_interval_seconds, remaining))
        return True

    def _rollback_loaded(self, labels: set[str]) -> None:
        rollback_incomplete: list[str] = []
        for label in sorted(labels):
            try:
                self._runner.run(("bootout", self._service_target(label)))
                if not self._wait_until_unloaded(label):
                    rollback_incomplete.append(label)
            except BaseException:
                rollback_incomplete.append(label)
        if rollback_incomplete:
            raise ConfigurationError(
                "CONTX LaunchAgent loading failed and rollback is incomplete: "
                + ", ".join(rollback_incomplete)
            )

    def _service_target(self, label: str) -> str:
        return f"{self._domain}/{label}"


def resolve_launch_agents_directory(*, home: Path | None = None) -> Path:
    """Resolve the current user's standard LaunchAgents directory."""
    resolved_home = (Path.home() if home is None else home).expanduser()
    if not resolved_home.is_absolute():
        raise ConfigurationError("The resolved home directory must be absolute")
    return resolved_home.resolve(strict=False) / "Library" / "LaunchAgents"


def _manifest_state(spec: LaunchAgentSpec) -> ManifestState:
    path = spec.path
    try:
        file_status = path.lstat()
    except FileNotFoundError:
        return ManifestState.MISSING
    except OSError:
        return ManifestState.UNSAFE
    if (
        not stat.S_ISREG(file_status.st_mode)
        or stat.S_IMODE(file_status.st_mode) & 0o077
    ):
        return ManifestState.UNSAFE
    if file_status.st_size > MAX_MANIFEST_BYTES:
        return ManifestState.UNSAFE
    try:
        content = path.read_bytes()
    except OSError:
        return ManifestState.UNSAFE
    return (
        ManifestState.MATCHING
        if content == spec.manifest
        else ManifestState.CONFLICTING
    )


def _require_matching_manifests(statuses: tuple[LaunchAgentStatus, ...]) -> None:
    invalid = tuple(
        status
        for status in statuses
        if status.manifest_state is not ManifestState.MATCHING
    )
    if invalid:
        labels = ", ".join(status.label for status in invalid)
        raise ConfigurationError(f"LaunchAgent manifests are not staged: {labels}")


def _ensure_managed_directory(path: Path) -> None:
    try:
        path.mkdir(mode=PRIVATE_DIRECTORY_MODE, parents=False, exist_ok=True)
        directory_status = path.lstat()
    except OSError as error:
        raise ConfigurationError(
            "Cannot prepare the user LaunchAgents directory"
        ) from error
    if (
        not stat.S_ISDIR(directory_status.st_mode)
        or directory_status.st_uid != os.getuid()
    ):
        raise ConfigurationError("The user LaunchAgents path is not a safe directory")
    if stat.S_IMODE(directory_status.st_mode) & 0o022:
        raise ConfigurationError(
            "The user LaunchAgents directory is writable by others"
        )


def _write_new_manifest(spec: LaunchAgentSpec) -> None:
    temporary = spec.path.with_name(f".{spec.path.name}.{uuid4().hex}.tmp")
    descriptor: int | None = None
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            PRIVATE_MANIFEST_MODE,
        )
        _write_all(descriptor, spec.manifest)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.link(temporary, spec.path, follow_symlinks=False)
        _fsync_directory(spec.path.parent)
    except FileExistsError as error:
        raise ConfigurationError(
            f"LaunchAgent manifest already exists: {spec.path.name}"
        ) from error
    except OSError as error:
        raise ConfigurationError(
            f"Cannot write LaunchAgent manifest: {spec.path.name}"
        ) from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
        with suppress(OSError):
            temporary.unlink(missing_ok=True)


def _unlink_matching_manifest(spec: LaunchAgentSpec) -> None:
    if _manifest_state(spec) is not ManifestState.MATCHING:
        raise ConfigurationError(
            f"Refusing to remove changed LaunchAgent: {spec.path.name}"
        )
    try:
        spec.path.unlink()
        _fsync_directory(spec.path.parent)
    except OSError as error:
        raise ConfigurationError(
            f"Cannot remove LaunchAgent manifest: {spec.path.name}"
        ) from error


def _write_all(descriptor: int, payload: bytes) -> None:
    remaining = memoryview(payload)
    while remaining:
        written = os.write(descriptor, remaining)
        if written == 0:
            raise OSError("zero-byte LaunchAgent manifest write")
        remaining = remaining[written:]


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _validate_launchctl_executable(path: Path) -> None:
    try:
        file_status = path.stat(follow_symlinks=False)
    except OSError as error:
        raise ConfigurationError("launchctl is unavailable") from error
    if not stat.S_ISREG(file_status.st_mode) or not os.access(path, os.X_OK):
        raise ConfigurationError("launchctl is not a safe executable")
