"""Bounded subprocess adapter for the reviewed OptMem snapshot."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import selectors
import stat
import subprocess
import tempfile
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path

from contx.errors import MemoryStoreError, MemoryStoreUnavailableError
from contx.memory_store.base import MemoryAppendResult, MemoryWake

OPTMEM_EXECUTABLE_ENV = "CONTX_OPTMEM_EXECUTABLE"
OPTMEM_SNAPSHOT_SHA256 = (
    "3dc120d01be3115ef6267eab4103e7909fc830d6227b549f20991ba999ee9ffb"
)
OPTMEM_ENTRY_BYTES = 280
DEFAULT_TIMEOUT_SECONDS = 5.0
DEFAULT_OUTPUT_BYTES = 32_768
IDEMPOTENCY_FILE = ".contx-idempotency.json"
LOCK_FILE = ".contx-adapter.lock"
_SAVED_PATTERN = re.compile(r"^Saved as #(\d+)\.$", re.MULTILINE)
_RECALL_PATTERN = re.compile(r"^#(\d+) \d{4}-\d{2}-\d{2} (.*)$", re.MULTILINE)
_PAGE_PATTERN = re.compile(
    r"^Your memory, part (\d+) of (\d+), oldest first \((\d+) memories\)\.$",
    re.MULTILINE,
)
_NEXT_PAGE_PATTERN = re.compile(
    r"^Not awake yet\. Run: .* wake (\d+) (\d+)$", re.MULTILINE
)


@dataclass(frozen=True, slots=True)
class _CommandResult:
    returncode: int
    stdout: str
    stderr: str


class OptMemAdapter:
    """Use one explicit OptMem identity without exposing its internals."""

    def __init__(
        self,
        *,
        executable: Path,
        memory_directory: Path,
        expected_sha256: str = OPTMEM_SNAPSHOT_SHA256,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        output_bytes: int = DEFAULT_OUTPUT_BYTES,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        if not executable.is_absolute():
            raise ValueError("OptMem executable path must be absolute")
        if not memory_directory.is_absolute():
            raise ValueError("OptMem memory path must be absolute")
        if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
            raise ValueError("OptMem checksum must be a lowercase SHA-256 digest")
        if timeout_seconds <= 0:
            raise ValueError("OptMem timeout must be positive")
        if output_bytes < 1:
            raise ValueError("OptMem output limit must be positive")
        self._executable = executable
        self._memory_directory = memory_directory
        self._expected_sha256 = expected_sha256
        self._timeout_seconds = timeout_seconds
        self._output_bytes = output_bytes
        source_environment = os.environ if environ is None else environ
        self._path = source_environment.get("PATH", "/usr/bin:/bin")
        self._home = source_environment.get("HOME", str(Path.home()))

    @property
    def memory_directory(self) -> Path:
        return self._memory_directory

    def initialize(self) -> None:
        self._memory_directory.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        result = self._run(("init",), allow_uninitialized=True)
        if result.returncode != 0:
            raise MemoryStoreError("OptMem could not initialize final memory")
        self._protect_store()

    def append(self, text: str, *, idempotency_key: str) -> MemoryAppendResult:
        normalized = _validate_memory_text(text)
        key = _key_digest(idempotency_key)
        with self._adapter_lock():
            mapping = self._read_idempotency_mapping()
            existing = mapping.get(key)
            if existing is not None:
                return MemoryAppendResult(backend_id=existing)

            recovered = self._find_exact_memory(normalized)
            if recovered is not None:
                mapping[key] = recovered
                self._write_idempotency_mapping(mapping)
                return MemoryAppendResult(backend_id=recovered)

            result = self._run(("note", normalized))
            if result.returncode != 0:
                raise MemoryStoreError("OptMem could not append final memory")
            match = _SAVED_PATTERN.search(result.stdout)
            if match is None:
                raise MemoryStoreError("OptMem returned an invalid append response")
            backend_id = match.group(1)
            mapping[key] = backend_id
            self._write_idempotency_mapping(mapping)
            self._protect_store()
            return MemoryAppendResult(
                backend_id=backend_id,
                maintenance_required=_maintenance_required(result.stdout),
            )

    def wake(self, *, part: int = 1, snapshot: int | None = None) -> MemoryWake:
        if part < 1:
            raise ValueError("wake part must be positive")
        if snapshot is not None and snapshot < 0:
            raise ValueError("wake snapshot must not be negative")
        arguments = ["wake", str(part)]
        if snapshot is not None:
            arguments.append(str(snapshot))
        result = self._run(tuple(arguments))
        maintenance_required = _maintenance_required(result.stdout)
        if result.returncode != 0 and not maintenance_required:
            raise MemoryStoreError("OptMem could not produce memory context")
        page = _PAGE_PATTERN.search(result.stdout)
        continuation = _NEXT_PAGE_PATTERN.search(result.stdout)
        resolved_snapshot = snapshot
        if page is not None:
            resolved_snapshot = int(page.group(3))
        elif continuation is not None:
            resolved_snapshot = int(continuation.group(2))
        return MemoryWake(
            content=result.stdout,
            complete="You are awake." in result.stdout,
            maintenance_required=maintenance_required,
            snapshot=resolved_snapshot,
            next_part=None if continuation is None else int(continuation.group(1)),
        )

    def recall(self, pattern: str) -> str:
        if not pattern or "\n" in pattern or "\r" in pattern:
            raise ValueError("recall pattern must be one non-empty line")
        result = self._run(("recall", pattern))
        if result.returncode != 0:
            raise MemoryStoreError("OptMem could not search final memory")
        return result.stdout

    def zoom(self, block: str) -> str:
        if re.fullmatch(r"\d+-\d+", block) is None:
            raise ValueError("zoom block must use the form <lo>-<hi>")
        result = self._run(("zoom", block))
        if result.returncode != 0:
            raise MemoryStoreError("OptMem could not navigate final memory")
        return result.stdout

    def _find_exact_memory(self, text: str) -> str | None:
        exact_pattern = rf"^#\d+ \d{{4}}-\d{{2}}-\d{{2}} {re.escape(text)}$"
        result = self._run(("recall", exact_pattern))
        if result.returncode != 0:
            raise MemoryStoreError("OptMem could not recover an interrupted append")
        matches = [
            match.group(1)
            for match in _RECALL_PATTERN.finditer(result.stdout)
            if match.group(2) == text
        ]
        return matches[-1] if matches else None

    @contextmanager
    def _adapter_lock(self) -> Iterator[None]:
        self._require_store()
        lock_path = self._memory_directory / LOCK_FILE
        try:
            descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        except OSError as error:
            raise MemoryStoreError("Cannot lock final memory") from error
        try:
            with os.fdopen(descriptor, "a+b", closefd=True) as lock_file:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                yield
        except OSError as error:
            raise MemoryStoreError("Cannot lock final memory") from error

    def _read_idempotency_mapping(self) -> dict[str, str]:
        path = self._memory_directory / IDEMPOTENCY_FILE
        if not path.exists():
            return {}
        if path.is_symlink() or not path.is_file():
            raise MemoryStoreError("OptMem idempotency state is unsafe")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise MemoryStoreError("OptMem idempotency state is unreadable") from error
        if not isinstance(payload, dict) or payload.get("version") != 1:
            raise MemoryStoreError("OptMem idempotency state is incompatible")
        entries = payload.get("entries")
        if not isinstance(entries, dict) or any(
            not isinstance(key, str)
            or re.fullmatch(r"[0-9a-f]{64}", key) is None
            or not isinstance(value, str)
            or not value.isdigit()
            for key, value in entries.items()
        ):
            raise MemoryStoreError("OptMem idempotency state is invalid")
        return dict(entries)

    def _write_idempotency_mapping(self, entries: Mapping[str, str]) -> None:
        path = self._memory_directory / IDEMPOTENCY_FILE
        temporary: Path | None = None
        try:
            descriptor, name = tempfile.mkstemp(
                prefix=f".{IDEMPOTENCY_FILE}.",
                suffix=".tmp",
                dir=self._memory_directory,
            )
            temporary = Path(name)
            os.fchmod(descriptor, 0o600)
            payload = json.dumps(
                {"version": 1, "entries": dict(entries)},
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            with os.fdopen(descriptor, "wb", closefd=True) as state_file:
                state_file.write(payload)
                state_file.flush()
                os.fsync(state_file.fileno())
            os.replace(temporary, path)
            temporary = None
            directory_descriptor = os.open(self._memory_directory, os.O_RDONLY)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
        except OSError as error:
            raise MemoryStoreError("Cannot persist OptMem idempotency state") from error
        finally:
            if temporary is not None:
                with suppress(OSError):
                    temporary.unlink(missing_ok=True)

    def _run(
        self,
        arguments: Sequence[str],
        *,
        allow_uninitialized: bool = False,
    ) -> _CommandResult:
        self._validate_executable()
        if not allow_uninitialized:
            self._require_store()
        environment = {
            "HOME": self._home,
            "MEMORY_DIR": str(self._memory_directory),
            "PATH": self._path,
            "PYTHONIOENCODING": "utf-8",
        }
        try:
            process = subprocess.Popen(
                (str(self._executable), *arguments),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=environment,
                close_fds=True,
            )
        except OSError as error:
            raise MemoryStoreUnavailableError("OptMem could not be started") from error
        try:
            stdout, stderr = self._collect_bounded_output(process)
        except Exception:
            if process.poll() is None:
                process.kill()
            process.wait()
            raise
        return _CommandResult(
            returncode=process.wait(),
            stdout=self._decode_output(stdout, "standard output"),
            stderr=self._decode_output(stderr, "error output"),
        )

    def _collect_bounded_output(
        self, process: subprocess.Popen[bytes]
    ) -> tuple[bytes, bytes]:
        if process.stdout is None or process.stderr is None:
            raise RuntimeError("OptMem process pipes were not created")
        streams = {process.stdout: bytearray(), process.stderr: bytearray()}
        deadline = time.monotonic() + self._timeout_seconds
        with selectors.DefaultSelector() as selector:
            for stream in streams:
                selector.register(stream, selectors.EVENT_READ)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise MemoryStoreError("OptMem exceeded its execution timeout")
                ready = selector.select(remaining)
                if not ready:
                    raise MemoryStoreError("OptMem exceeded its execution timeout")
                for key, _events in ready:
                    stream = (
                        process.stdout
                        if key.fd == process.stdout.fileno()
                        else process.stderr
                    )
                    chunk = os.read(key.fd, 65_536)
                    if not chunk:
                        selector.unregister(stream)
                        continue
                    target = streams[stream]
                    target.extend(chunk)
                    if len(target) > self._output_bytes:
                        label = (
                            "standard output"
                            if stream is process.stdout
                            else "error output"
                        )
                        raise MemoryStoreError(
                            f"OptMem {label} exceeded the safe output limit"
                        )
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise MemoryStoreError("OptMem exceeded its execution timeout")
        try:
            process.wait(timeout=remaining)
        except subprocess.TimeoutExpired as error:
            raise MemoryStoreError("OptMem exceeded its execution timeout") from error
        return bytes(streams[process.stdout]), bytes(streams[process.stderr])

    @staticmethod
    def _decode_output(payload: bytes, label: str) -> str:
        try:
            return payload.decode("utf-8")
        except UnicodeDecodeError as error:
            raise MemoryStoreError(f"OptMem {label} was not valid UTF-8") from error

    def _validate_executable(self) -> None:
        path = self._executable
        try:
            file_status = path.stat(follow_symlinks=False)
        except OSError as error:
            raise MemoryStoreUnavailableError(
                "OptMem executable is unavailable; set CONTX_OPTMEM_EXECUTABLE"
            ) from error
        if path.is_symlink() or not stat.S_ISREG(file_status.st_mode):
            raise MemoryStoreUnavailableError(
                "OptMem executable must be a regular non-symlink file"
            )
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as error:
            raise MemoryStoreUnavailableError(
                "OptMem executable cannot be verified"
            ) from error
        if digest != self._expected_sha256:
            raise MemoryStoreUnavailableError(
                "OptMem executable does not match the reviewed snapshot"
            )
        if not os.access(path, os.X_OK):
            raise MemoryStoreUnavailableError("OptMem executable is not executable")

    def _require_store(self) -> None:
        path = self._memory_directory
        if path.is_symlink() or not path.is_dir():
            raise MemoryStoreUnavailableError(
                "Final memory is not initialized; run a CONTX initialization cycle"
            )

    def _protect_store(self) -> None:
        try:
            for root, directories, files in os.walk(
                self._memory_directory, followlinks=False
            ):
                root_path = Path(root)
                if root_path.is_symlink():
                    raise MemoryStoreError("OptMem created an unsafe directory link")
                root_path.chmod(0o700)
                for name in directories:
                    directory = root_path / name
                    if directory.is_symlink():
                        raise MemoryStoreError(
                            "OptMem created an unsafe directory link"
                        )
                    directory.chmod(0o700)
                for name in files:
                    file_path = root_path / name
                    if file_path.is_symlink():
                        raise MemoryStoreError("OptMem created an unsafe file link")
                    file_path.chmod(0o600)
        except OSError as error:
            raise MemoryStoreError("Cannot protect OptMem state") from error


def resolve_optmem_executable(
    environ: Mapping[str, str] | None = None,
    *,
    home: Path | None = None,
) -> Path:
    """Resolve an explicit override, development snapshot, or user install."""
    environment = os.environ if environ is None else environ
    override = environment.get(OPTMEM_EXECUTABLE_ENV)
    if override is not None:
        if not override.strip():
            raise MemoryStoreUnavailableError(
                f"{OPTMEM_EXECUTABLE_ENV} must not be empty"
            )
        path = Path(override).expanduser()
        if not path.is_absolute():
            raise MemoryStoreUnavailableError(
                f"{OPTMEM_EXECUTABLE_ENV} must be an absolute path"
            )
        return path.resolve(strict=False)

    repository_reference = Path(__file__).resolve().parents[2] / "optmem" / "memo"
    if repository_reference.is_file():
        return repository_reference

    resolved_home = Path.home() if home is None else home
    return resolved_home.expanduser().resolve(strict=False) / ".optmem" / "memo"


def _validate_memory_text(text: str) -> str:
    normalized = text.strip()
    if not normalized or "\n" in normalized or "\r" in normalized:
        raise MemoryStoreError("Final memory must be one non-empty line")
    if len(normalized.encode("utf-8")) > OPTMEM_ENTRY_BYTES:
        raise MemoryStoreError(
            f"Final memory exceeds OptMem's {OPTMEM_ENTRY_BYTES}-byte limit"
        )
    return normalized


def _key_digest(key: str) -> str:
    if not key:
        raise ValueError("memory idempotency key must not be empty")
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _maintenance_required(output: str) -> bool:
    return "Cannot wake:" in output or bool(
        re.search(r"^Run: .* nap(?: |$)", output, re.MULTILINE)
    )
