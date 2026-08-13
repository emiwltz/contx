"""Atomic active-only OptMem projection used by semantic wake."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, replace
from pathlib import Path
from uuid import uuid4

from sqlalchemy import Engine

from contx.db import immediate_session_scope, session_scope
from contx.db.repositories import PipelineRepository
from contx.errors import MemoryStoreError
from contx.memory_store import MemoryCompressor, MemoryStore, MemoryWake
from contx.models import MemoryCandidate, MemoryLink, MemoryLinkStatus

ACTIVE_MEMORY_PROJECTION_VERSION = "active-memory-projection-v1"
PROJECTION_SCHEMA_VERSION = 2
CURRENT_POINTER_FILE = "CURRENT.json"
PROJECTION_LOCK_FILE = ".projection.lock"
GENERATIONS_DIRECTORY = "generations"
READY_FILE = "READY.json"
MAX_REBUILD_ATTEMPTS = 2
MAX_RETAINED_GENERATIONS = 2
_FINGERPRINT_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_BUILD_DIRECTORY_PATTERN = re.compile(r"^\.building-[0-9a-f]{32}$")

MemoryStoreFactory = Callable[[Path], MemoryStore]
ActiveMemoryRecord = tuple[MemoryLink, MemoryCandidate]


@dataclass(frozen=True, slots=True)
class ActiveMemoryProjectionResult:
    """Observable result of synchronizing one active projection generation."""

    fingerprint: str
    generation: str
    active_memory_count: int
    rebuilt: bool
    completed_compressions: int


@dataclass(frozen=True, slots=True)
class ActiveMemoryWakeResult:
    """Direct OptMem wake output plus projection synchronization metadata."""

    wake: MemoryWake
    projection: ActiveMemoryProjectionResult


@dataclass(frozen=True, slots=True)
class _ProjectionPointer:
    fingerprint: str
    generation: str
    active_memory_count: int


class ActiveMemoryProjectionService:
    """Publish a complete OptMem projection of only active SQLite memories."""

    def __init__(
        self,
        *,
        engine: Engine,
        projection_root: Path,
        memory_store_factory: MemoryStoreFactory,
        compressor: MemoryCompressor,
    ) -> None:
        if not projection_root.is_absolute():
            raise ValueError("active memory projection path must be absolute")
        self._engine = engine
        self._projection_root = projection_root
        self._memory_store_factory = memory_store_factory
        self._compressor = compressor

    def wake(
        self,
        *,
        part: int = 1,
        snapshot: int | None = None,
    ) -> ActiveMemoryWakeResult:
        if part < 1:
            raise ValueError("wake part must be positive")
        if snapshot is not None and snapshot < 0:
            raise ValueError("wake snapshot must not be negative")
        if snapshot is None:
            projection, memory_store = self._synchronize()
        else:
            projection, memory_store = self._projection_for_snapshot(snapshot)
        projection_snapshot = _projection_snapshot(projection.generation)
        if projection.active_memory_count == 0:
            if part != 1:
                raise ValueError("empty active memory has only part 1")
            return ActiveMemoryWakeResult(
                wake=MemoryWake(
                    content="",
                    complete=True,
                    technical_status="No active memories yet.",
                    snapshot=projection_snapshot,
                ),
                projection=projection,
            )
        wake = memory_store.wake(
            part=part,
            snapshot=(None if snapshot is None else projection.active_memory_count),
        )
        return ActiveMemoryWakeResult(
            wake=replace(wake, snapshot=projection_snapshot),
            projection=projection,
        )

    def synchronize(self) -> ActiveMemoryProjectionResult:
        projection, _memory_store = self._synchronize(force_rebuild=False)
        return projection

    def rebuild(self) -> ActiveMemoryProjectionResult:
        projection, _memory_store = self._synchronize(force_rebuild=True)
        return projection

    def _synchronize(
        self,
        *,
        force_rebuild: bool = False,
    ) -> tuple[ActiveMemoryProjectionResult, MemoryStore]:
        self._ensure_projection_root()
        with self._projection_lock():
            self._discard_interrupted_builds()
            for _attempt in range(MAX_REBUILD_ATTEMPTS):
                records = self._active_records()
                fingerprint = _projection_fingerprint(records)
                pointer = self._read_pointer()
                if (
                    not force_rebuild
                    and pointer is not None
                    and pointer.fingerprint == fingerprint
                    and pointer.active_memory_count == len(records)
                ):
                    existing = self._ready_store(pointer)
                    if existing is not None:
                        existing.initialize()
                        return (
                            ActiveMemoryProjectionResult(
                                fingerprint=fingerprint,
                                generation=pointer.generation,
                                active_memory_count=len(records),
                                rebuilt=False,
                                completed_compressions=0,
                            ),
                            existing,
                        )

                ready = (
                    None
                    if force_rebuild
                    else self._latest_ready_generation(
                        fingerprint=fingerprint,
                        active_memory_count=len(records),
                    )
                )
                if ready is not None:
                    ready_pointer, existing = ready
                    existing.initialize()
                    self._publish_pointer(ready_pointer)
                    self._prune_generations(current=ready_pointer.generation)
                    return (
                        ActiveMemoryProjectionResult(
                            fingerprint=fingerprint,
                            generation=ready_pointer.generation,
                            active_memory_count=len(records),
                            rebuilt=False,
                            completed_compressions=0,
                        ),
                        existing,
                    )

                generation_id = _generation_identity(fingerprint)
                built = self._build_generation(
                    records,
                    fingerprint=fingerprint,
                    generation=generation_id,
                )
                generation = self._generation_path(generation_id)
                try:
                    with immediate_session_scope(self._engine) as database_session:
                        refreshed = PipelineRepository(
                            database_session
                        ).active_memory_records()
                        if _projection_fingerprint(refreshed) != fingerprint:
                            self._remove_derived_directory(built.memory_directory)
                            continue
                        os.replace(built.memory_directory, generation)
                        _fsync_directory(generation.parent)
                        self._publish_pointer(
                            _ProjectionPointer(
                                fingerprint=fingerprint,
                                generation=generation_id,
                                active_memory_count=len(records),
                            )
                        )
                        self._prune_generations(current=generation_id)
                except OSError as error:
                    self._remove_derived_directory(built.memory_directory)
                    raise MemoryStoreError(
                        "Cannot publish active memory projection generation"
                    ) from error
                published = self._memory_store_factory(generation)
                published.initialize()
                return (
                    ActiveMemoryProjectionResult(
                        fingerprint=fingerprint,
                        generation=generation_id,
                        active_memory_count=len(records),
                        rebuilt=True,
                        completed_compressions=built.completed_compressions,
                    ),
                    published,
                )
        raise MemoryStoreError(
            "Active memory changed repeatedly while its projection was rebuilt"
        )

    def _projection_for_snapshot(
        self,
        snapshot: int,
    ) -> tuple[ActiveMemoryProjectionResult, MemoryStore]:
        self._ensure_projection_root()
        matches: list[tuple[_ProjectionPointer, MemoryStore]] = []
        with self._projection_lock():
            try:
                children = tuple(self._generations_path.iterdir())
            except OSError as error:
                raise MemoryStoreError(
                    "Cannot inspect active memory projection snapshots"
                ) from error
            for child in children:
                if _FINGERPRINT_PATTERN.fullmatch(child.name) is None:
                    continue
                if child.is_symlink() or not child.is_dir():
                    raise MemoryStoreError(
                        "Active memory projection generation is unsafe"
                    )
                ready = _read_private_json(child / READY_FILE, missing_ok=True)
                pointer = _parse_pointer_payload(ready)
                if (
                    pointer is None
                    or _projection_snapshot(pointer.generation) != snapshot
                ):
                    continue
                store = self._ready_store(pointer)
                if store is not None:
                    matches.append((pointer, store))
            if len(matches) != 1:
                raise MemoryStoreError(
                    "Active memory projection snapshot is unavailable; "
                    "restart contx wake"
                )
            pointer, store = matches[0]
            store.initialize()
            return (
                ActiveMemoryProjectionResult(
                    fingerprint=pointer.fingerprint,
                    generation=pointer.generation,
                    active_memory_count=pointer.active_memory_count,
                    rebuilt=False,
                    completed_compressions=0,
                ),
                store,
            )

    def _active_records(self) -> tuple[ActiveMemoryRecord, ...]:
        with session_scope(self._engine) as database_session:
            records = PipelineRepository(database_session).active_memory_records()
        if any(link.status is not MemoryLinkStatus.ACTIVE for link, _ in records):
            raise MemoryStoreError("Active memory projection source is inconsistent")
        return records

    def _build_generation(
        self,
        records: tuple[ActiveMemoryRecord, ...],
        *,
        fingerprint: str,
        generation: str,
    ) -> _BuiltGeneration:
        generations = self._generations_path
        generations.mkdir(mode=0o700, parents=True, exist_ok=True)
        generations.chmod(0o700)
        temporary = generations / f".building-{uuid4().hex}"
        temporary.mkdir(mode=0o700)
        memory_store = self._memory_store_factory(temporary)
        completed = 0
        try:
            memory_store.initialize()
            for link, candidate in records:
                memory_store.append(
                    candidate.text,
                    idempotency_key=(
                        f"{ACTIVE_MEMORY_PROJECTION_VERSION}:{link.id}:"
                        f"{candidate.idempotency_key}"
                    ),
                )
            while True:
                maintenance = memory_store.maintain(
                    self._compressor,
                    max_compressions=100,
                )
                completed += maintenance.completed_compressions
                if completed > max(0, len(records) - 1):
                    raise MemoryStoreError(
                        "Active memory projection exceeded its compression bound"
                    )
                if maintenance.complete:
                    break
                if maintenance.completed_compressions == 0:
                    raise MemoryStoreError(
                        "Active memory projection compression made no progress"
                    )
            self._write_private_json(
                temporary / READY_FILE,
                _pointer_payload(
                    fingerprint,
                    generation,
                    len(records),
                ),
            )
            _fsync_directory(temporary)
        except Exception:
            self._remove_derived_directory(temporary)
            raise
        return _BuiltGeneration(
            memory_directory=temporary,
            completed_compressions=completed,
        )

    def _ready_store(self, pointer: _ProjectionPointer) -> MemoryStore | None:
        generation = self._generation_path(pointer.generation)
        if not generation.exists():
            return None
        if generation.is_symlink() or not generation.is_dir():
            raise MemoryStoreError("Active memory projection generation is unsafe")
        ready = _read_private_json(generation / READY_FILE, missing_ok=True)
        if ready != _pointer_payload(
            pointer.fingerprint,
            pointer.generation,
            pointer.active_memory_count,
        ):
            self._remove_derived_directory(generation)
            return None
        return self._memory_store_factory(generation)

    def _latest_ready_generation(
        self,
        *,
        fingerprint: str,
        active_memory_count: int,
    ) -> tuple[_ProjectionPointer, MemoryStore] | None:
        matches: list[tuple[int, _ProjectionPointer, MemoryStore]] = []
        try:
            children = tuple(self._generations_path.iterdir())
        except OSError as error:
            raise MemoryStoreError(
                "Cannot inspect active memory projection generations"
            ) from error
        for child in children:
            if _FINGERPRINT_PATTERN.fullmatch(child.name) is None:
                continue
            if child.is_symlink() or not child.is_dir():
                raise MemoryStoreError("Active memory projection generation is unsafe")
            pointer = _parse_pointer_payload(
                _read_private_json(child / READY_FILE, missing_ok=True)
            )
            if (
                pointer is None
                or pointer.generation != child.name
                or pointer.fingerprint != fingerprint
                or pointer.active_memory_count != active_memory_count
            ):
                continue
            store = self._ready_store(pointer)
            if store is not None:
                matches.append(
                    (
                        child.stat(follow_symlinks=False).st_mtime_ns,
                        pointer,
                        store,
                    )
                )
        if not matches:
            return None
        _modified, pointer, store = max(matches, key=lambda value: value[0])
        return pointer, store

    def _read_pointer(self) -> _ProjectionPointer | None:
        payload = _read_private_json(self._pointer_path, missing_ok=True)
        return _parse_pointer_payload(payload)

    def _publish_pointer(self, pointer: _ProjectionPointer) -> None:
        self._write_private_json(
            self._pointer_path,
            _pointer_payload(
                pointer.fingerprint,
                pointer.generation,
                pointer.active_memory_count,
            ),
        )
        _fsync_directory(self._projection_root)

    def _write_private_json(self, path: Path, payload: dict[str, object]) -> None:
        temporary: Path | None = None
        try:
            descriptor, name = tempfile.mkstemp(
                prefix=f".{path.name}.",
                suffix=".tmp",
                dir=path.parent,
            )
            temporary = Path(name)
            os.fchmod(descriptor, 0o600)
            encoded = json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            with os.fdopen(descriptor, "wb", closefd=True) as output:
                output.write(encoded)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
            temporary = None
        except OSError as error:
            raise MemoryStoreError(
                "Cannot persist active memory projection state"
            ) from error
        finally:
            if temporary is not None:
                with suppress(OSError):
                    temporary.unlink(missing_ok=True)

    def _ensure_projection_root(self) -> None:
        try:
            self._projection_root.mkdir(mode=0o700, parents=True, exist_ok=True)
            if self._projection_root.is_symlink() or not self._projection_root.is_dir():
                raise MemoryStoreError("Active memory projection path is unsafe")
            self._projection_root.chmod(0o700)
            self._generations_path.mkdir(mode=0o700, exist_ok=True)
            if (
                self._generations_path.is_symlink()
                or not self._generations_path.is_dir()
            ):
                raise MemoryStoreError("Active memory generation path is unsafe")
            self._generations_path.chmod(0o700)
        except OSError as error:
            raise MemoryStoreError(
                "Cannot initialize active memory projection"
            ) from error

    @contextmanager
    def _projection_lock(self) -> Iterator[None]:
        lock = self._projection_root / PROJECTION_LOCK_FILE
        if lock.is_symlink():
            raise MemoryStoreError("Active memory projection lock is unsafe")
        try:
            descriptor = os.open(
                lock,
                os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                os.close(descriptor)
                raise MemoryStoreError("Active memory projection lock is unsafe")
            with os.fdopen(descriptor, "a+b", closefd=True) as lock_file:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                yield
        except OSError as error:
            raise MemoryStoreError("Cannot lock active memory projection") from error

    def _discard_interrupted_builds(self) -> None:
        try:
            children = tuple(self._generations_path.iterdir())
        except OSError as error:
            raise MemoryStoreError("Cannot inspect active memory projection") from error
        for child in children:
            if _BUILD_DIRECTORY_PATTERN.fullmatch(child.name) is None:
                continue
            self._remove_derived_directory(child)

    def _prune_generations(self, *, current: str) -> None:
        generations: list[tuple[int, Path]] = []
        try:
            for child in self._generations_path.iterdir():
                if _FINGERPRINT_PATTERN.fullmatch(child.name) is None:
                    continue
                if child.is_symlink() or not child.is_dir():
                    raise MemoryStoreError(
                        "Active memory projection generation is unsafe"
                    )
                generations.append(
                    (child.stat(follow_symlinks=False).st_mtime_ns, child)
                )
        except OSError as error:
            raise MemoryStoreError(
                "Cannot inspect active memory generations"
            ) from error
        retained = {current}
        for _modified, path in sorted(generations, reverse=True):
            if len(retained) >= MAX_RETAINED_GENERATIONS:
                break
            retained.add(path.name)
        for _modified, path in generations:
            if path.name not in retained:
                self._remove_derived_directory(path)

    def _remove_derived_directory(self, path: Path) -> None:
        if path.parent != self._generations_path:
            raise MemoryStoreError("Refusing to remove an unrelated directory")
        try:
            status = path.stat(follow_symlinks=False)
        except FileNotFoundError:
            return
        except OSError as error:
            raise MemoryStoreError("Cannot inspect derived projection state") from error
        if path.is_symlink() or not stat.S_ISDIR(status.st_mode):
            raise MemoryStoreError("Derived projection state is unsafe")
        try:
            shutil.rmtree(path)
        except OSError as error:
            raise MemoryStoreError("Cannot remove derived projection state") from error

    def _generation_path(self, fingerprint: str) -> Path:
        if _FINGERPRINT_PATTERN.fullmatch(fingerprint) is None:
            raise MemoryStoreError("Active memory projection identity is invalid")
        return self._generations_path / fingerprint

    @property
    def _generations_path(self) -> Path:
        return self._projection_root / GENERATIONS_DIRECTORY

    @property
    def _pointer_path(self) -> Path:
        return self._projection_root / CURRENT_POINTER_FILE


@dataclass(frozen=True, slots=True)
class _BuiltGeneration:
    memory_directory: Path
    completed_compressions: int


def _projection_fingerprint(records: tuple[ActiveMemoryRecord, ...]) -> str:
    digest = hashlib.sha256()
    digest.update(ACTIVE_MEMORY_PROJECTION_VERSION.encode("ascii"))
    for link, candidate in records:
        digest.update(b"\0")
        digest.update(str(link.id).encode("ascii"))
        digest.update(b"\0")
        digest.update(str(candidate.id).encode("ascii"))
        digest.update(b"\0")
        digest.update(candidate.idempotency_key.encode("ascii"))
        digest.update(b"\0")
        digest.update(candidate.text.encode("utf-8"))
    return digest.hexdigest()


def _generation_identity(fingerprint: str) -> str:
    if _FINGERPRINT_PATTERN.fullmatch(fingerprint) is None:
        raise MemoryStoreError("Active memory projection identity is invalid")
    return hashlib.sha256(f"{fingerprint}:{uuid4().hex}".encode("ascii")).hexdigest()


def _pointer_payload(
    fingerprint: str,
    generation: str,
    count: int,
) -> dict[str, object]:
    return {
        "schema_version": PROJECTION_SCHEMA_VERSION,
        "fingerprint": fingerprint,
        "generation": generation,
        "active_memory_count": count,
    }


def _parse_pointer_payload(payload: object | None) -> _ProjectionPointer | None:
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != PROJECTION_SCHEMA_VERSION
    ):
        return None
    fingerprint = payload.get("fingerprint")
    generation = payload.get("generation")
    count = payload.get("active_memory_count")
    if (
        not isinstance(fingerprint, str)
        or _FINGERPRINT_PATTERN.fullmatch(fingerprint) is None
        or not isinstance(generation, str)
        or _FINGERPRINT_PATTERN.fullmatch(generation) is None
        or not isinstance(count, int)
        or isinstance(count, bool)
        or count < 0
    ):
        return None
    return _ProjectionPointer(
        fingerprint=fingerprint,
        generation=generation,
        active_memory_count=count,
    )


def _projection_snapshot(generation: str) -> int:
    if _FINGERPRINT_PATTERN.fullmatch(generation) is None:
        raise MemoryStoreError("Active memory projection identity is invalid")
    return int(generation[:15], 16) or 1


def _read_private_json(path: Path, *, missing_ok: bool) -> object | None:
    try:
        status = path.stat(follow_symlinks=False)
    except FileNotFoundError:
        if missing_ok:
            return None
        raise MemoryStoreError("Active memory projection state is missing") from None
    except OSError as error:
        raise MemoryStoreError(
            "Cannot inspect active memory projection state"
        ) from error
    if path.is_symlink() or not stat.S_ISREG(status.st_mode):
        raise MemoryStoreError("Active memory projection state is unsafe")
    try:
        payload: object = json.loads(path.read_text(encoding="utf-8"))
        return payload
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError as error:
        raise MemoryStoreError("Cannot synchronize active memory projection") from error
