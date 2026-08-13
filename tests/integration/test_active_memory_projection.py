"""Active-only OptMem projection correctness and recovery tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import Engine

from contx.application import ActiveMemoryProjectionService, MemoryCorrectionService
from contx.db import create_database_engine, session_scope, upgrade_database
from contx.db.repositories import PipelineRepository
from contx.errors import MemoryStoreError
from contx.memory_store import (
    MemoryAppendResult,
    MemoryCompressionRequest,
    MemoryCompressor,
    MemoryMaintenance,
    MemoryWake,
)
from contx.models import (
    CandidateStatus,
    EpistemicStatus,
    Event,
    EventType,
    MemoryCandidate,
    MemoryLink,
    MemoryProvenance,
    Observation,
    Sensitivity,
    SourceType,
)
from tests.helpers import FixedClock

NOW = datetime(2026, 8, 2, 22, 0, tzinfo=UTC)
OBSERVATION_ID = UUID("d0000000-0000-0000-0000-000000000001")
EVENT_ID = UUID("d0000000-0000-0000-0000-000000000002")
OLD_CANDIDATE_ID = UUID("d0000000-0000-0000-0000-000000000003")
OLD_MEMORY_ID = UUID("d0000000-0000-0000-0000-000000000004")
INDEPENDENT_CANDIDATE_ID = UUID("d0000000-0000-0000-0000-000000000005")
INDEPENDENT_MEMORY_ID = UUID("d0000000-0000-0000-0000-000000000006")
NEW_CANDIDATE_ID = UUID("d0000000-0000-0000-0000-000000000007")
NEW_MEMORY_ID = UUID("d0000000-0000-0000-0000-000000000008")


def test_wake_projects_only_active_exact_text_and_reuses_generation(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path)
    historical = PersistentMemoryStore(tmp_path / "historical")
    factory = PersistentStoreFactory()
    compressor = RecordingCompressor()
    try:
        old = _seed_source_memories(engine, historical)
        corrected = MemoryCorrectionService(
            engine=engine,
            memory_store=historical,
            composer=StaticCorrectionComposer(),
            clock=FixedClock(NOW + timedelta(minutes=1)),
        ).correct(
            memory_id=old.id,
            replacement="Atlas deploys to edge-only.",
        )
        service = ActiveMemoryProjectionService(
            engine=engine,
            projection_root=tmp_path / "active-projection",
            memory_store_factory=factory,
            compressor=compressor,
        )

        first = service.wake()
        second = service.wake()

        assert first.projection.rebuilt
        assert first.projection.active_memory_count == 2
        assert first.projection.completed_compressions == 1
        assert "Atlas deploys to staging" not in first.wake.content
        assert corrected.candidate.text in first.wake.content
        assert "Atlas runtime requires Python 3.12." in first.wake.content
        assert not second.projection.rebuilt
        assert second.projection.fingerprint == first.projection.fingerprint
        assert second.wake.content == first.wake.content
        assert len(compressor.requests) == 1
        assert historical.entries == (
            "Atlas deploys to staging.",
            "Atlas runtime requires Python 3.12.",
            corrected.candidate.text,
        )
    finally:
        engine.dispose()


def test_failed_rebuild_keeps_previous_generation_publishable(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path)
    historical = PersistentMemoryStore(tmp_path / "historical")
    factory = PersistentStoreFactory()
    projection_root = tmp_path / "active-projection"
    try:
        _seed_source_memories(engine, historical)
        service = ActiveMemoryProjectionService(
            engine=engine,
            projection_root=projection_root,
            memory_store_factory=factory,
            compressor=RecordingCompressor(),
        )
        initial = service.synchronize()
        pointer_before = (projection_root / "CURRENT.json").read_bytes()
        _seed_additional_memory(engine, historical)
        factory.fail_new_builds = True

        with pytest.raises(MemoryStoreError, match="injected projection failure"):
            service.synchronize()

        assert (projection_root / "CURRENT.json").read_bytes() == pointer_before
        current = json.loads(pointer_before)
        current_generation = projection_root / "generations" / current["generation"]
        assert current_generation.is_dir()
        assert PersistentMemoryStore(current_generation).entries == (
            "Atlas deploys to staging.",
            "Atlas runtime requires Python 3.12.",
        )

        factory.fail_new_builds = False
        recovered = service.synchronize()

        assert recovered.rebuilt
        assert recovered.fingerprint != initial.fingerprint
        assert recovered.active_memory_count == 3
    finally:
        engine.dispose()


def test_corrupt_pointer_is_recovered_from_ready_generation(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    historical = PersistentMemoryStore(tmp_path / "historical")
    projection_root = tmp_path / "active-projection"
    try:
        _seed_source_memories(engine, historical)
        service = ActiveMemoryProjectionService(
            engine=engine,
            projection_root=projection_root,
            memory_store_factory=PersistentStoreFactory(),
            compressor=RecordingCompressor(),
        )
        initial = service.synchronize()
        (projection_root / "CURRENT.json").write_text("not-json", encoding="utf-8")

        recovered = service.synchronize()
        pointer = json.loads(
            (projection_root / "CURRENT.json").read_text(encoding="utf-8")
        )

        assert not recovered.rebuilt
        assert recovered.fingerprint == initial.fingerprint
        assert recovered.generation == initial.generation
        assert pointer["fingerprint"] == initial.fingerprint
        assert pointer["generation"] == initial.generation
    finally:
        engine.dispose()


def test_source_change_during_build_is_retried_before_publication(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path)
    historical = PersistentMemoryStore(tmp_path / "historical")
    projection_root = tmp_path / "active-projection"
    try:
        _seed_source_memories(engine, historical)
        compressor = MutatingCompressor(engine=engine, historical=historical)
        result = ActiveMemoryProjectionService(
            engine=engine,
            projection_root=projection_root,
            memory_store_factory=PersistentStoreFactory(),
            compressor=compressor,
        ).wake()

        assert result.projection.rebuilt
        assert result.projection.active_memory_count == 3
        assert result.projection.completed_compressions == 2
        assert "Atlas documentation is maintained in English." in result.wake.content
        assert len(compressor.requests) == 3
        generations = tuple((projection_root / "generations").iterdir())
        assert len(generations) == 1
        assert not generations[0].name.startswith(".building-")
    finally:
        engine.dispose()


def test_forced_rebuild_publishes_new_generation_and_bounds_retention(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path)
    historical = PersistentMemoryStore(tmp_path / "historical")
    projection_root = tmp_path / "active-projection"
    compressor = RecordingCompressor()
    try:
        _seed_source_memories(engine, historical)
        service = ActiveMemoryProjectionService(
            engine=engine,
            projection_root=projection_root,
            memory_store_factory=PersistentStoreFactory(),
            compressor=compressor,
        )

        initial = service.synchronize()
        second = service.rebuild()
        third = service.rebuild()
        reused = service.synchronize()

        assert initial.fingerprint == second.fingerprint == third.fingerprint
        assert len({initial.generation, second.generation, third.generation}) == 3
        assert second.rebuilt and third.rebuilt
        assert reused.generation == third.generation
        assert not reused.rebuilt
        assert len(compressor.requests) == 3
        generations = tuple((projection_root / "generations").iterdir())
        assert {path.name for path in generations} == {
            second.generation,
            third.generation,
        }
    finally:
        engine.dispose()


def test_pagination_resumes_the_original_generation_after_active_change(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path)
    historical = PersistentMemoryStore(tmp_path / "historical")
    projection_root = tmp_path / "active-projection"
    try:
        _seed_source_memories(engine, historical)
        service = ActiveMemoryProjectionService(
            engine=engine,
            projection_root=projection_root,
            memory_store_factory=PaginatedStoreFactory(),
            compressor=RecordingCompressor(),
        )

        first = service.wake()
        assert first.wake.next_part == 2
        first_snapshot = first.wake.snapshot
        assert first_snapshot is not None
        assert first_snapshot != first.projection.active_memory_count

        _seed_additional_memory(engine, historical)
        changed = service.wake()
        resumed = service.wake(part=2, snapshot=first_snapshot)

        assert changed.projection.fingerprint != first.projection.fingerprint
        assert resumed.projection.fingerprint == first.projection.fingerprint
        assert resumed.wake.snapshot == first_snapshot
        assert "Atlas runtime requires Python 3.12." in resumed.wake.content
        assert "documentation" not in resumed.wake.content.casefold()
    finally:
        engine.dispose()


def test_symlinked_pointer_is_rejected_without_rebuilding(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    historical = PersistentMemoryStore(tmp_path / "historical")
    projection_root = tmp_path / "active-projection"
    try:
        _seed_source_memories(engine, historical)
        service = ActiveMemoryProjectionService(
            engine=engine,
            projection_root=projection_root,
            memory_store_factory=PersistentStoreFactory(),
            compressor=RecordingCompressor(),
        )
        service.synchronize()
        pointer = projection_root / "CURRENT.json"
        target = tmp_path / "outside-pointer.json"
        target.write_text("{}", encoding="utf-8")
        pointer.unlink()
        pointer.symlink_to(target)

        with pytest.raises(MemoryStoreError, match="state is unsafe"):
            service.synchronize()

        assert target.read_text(encoding="utf-8") == "{}"
    finally:
        engine.dispose()


def test_empty_active_set_has_a_complete_empty_wake(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    try:
        result = ActiveMemoryProjectionService(
            engine=engine,
            projection_root=tmp_path / "active-projection",
            memory_store_factory=PersistentStoreFactory(),
            compressor=RecordingCompressor(),
        ).wake()

        assert result.projection.active_memory_count == 0
        assert result.projection.rebuilt
        assert result.wake.complete
        assert result.wake.content == ""
        assert result.wake.snapshot is not None
        assert result.wake.snapshot > 0
        with pytest.raises(ValueError, match="only part 1"):
            ActiveMemoryProjectionService(
                engine=engine,
                projection_root=tmp_path / "active-projection",
                memory_store_factory=PersistentStoreFactory(),
                compressor=RecordingCompressor(),
            ).wake(part=2, snapshot=result.wake.snapshot)
    finally:
        engine.dispose()


class StaticCorrectionComposer:
    provider = "ollama"
    endpoint = "http://127.0.0.1:11434"
    model = "synthetic-local-model"
    model_digest = "synthetic-digest"
    prompt_version = "memory-correction-v1"
    output_schema_version = "memory-correction-output-v1"

    def compose(
        self,
        *,
        original: str,
        replacement: str,
        max_bytes: int,
    ) -> str:
        return "Correction: Atlas deploys to edge-only."


class RecordingCompressor:
    def __init__(self) -> None:
        self.requests: list[MemoryCompressionRequest] = []

    def compress(self, request: MemoryCompressionRequest) -> str:
        self.requests.append(request)
        return "Synthetic active-memory summary."


class MutatingCompressor(RecordingCompressor):
    def __init__(self, *, engine: Engine, historical: PersistentMemoryStore) -> None:
        super().__init__()
        self._engine = engine
        self._historical = historical
        self._mutated = False

    def compress(self, request: MemoryCompressionRequest) -> str:
        result = super().compress(request)
        if not self._mutated:
            self._mutated = True
            _seed_additional_memory(self._engine, self._historical)
        return result


class PersistentStoreFactory:
    def __init__(self) -> None:
        self.fail_new_builds = False

    def __call__(self, path: Path) -> PersistentMemoryStore:
        return PersistentMemoryStore(
            path,
            fail_append=self.fail_new_builds and path.name.startswith(".building-"),
        )


class PaginatedStoreFactory:
    def __call__(self, path: Path) -> PaginatedPersistentMemoryStore:
        return PaginatedPersistentMemoryStore(path)


class PersistentMemoryStore:
    """Small filesystem-backed test double that survives directory renames."""

    def __init__(self, root: Path, *, fail_append: bool = False) -> None:
        self._root = root
        self._fail_append = fail_append

    @property
    def entries(self) -> tuple[str, ...]:
        state = self._read_state()
        return tuple(str(value) for value in state["entries"])

    def initialize(self) -> None:
        self._root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not self._state_path.exists():
            self._write_state({"entries": [], "keys": {}, "compressed": False})

    def append(self, text: str, *, idempotency_key: str) -> MemoryAppendResult:
        if self._fail_append:
            raise MemoryStoreError("injected projection failure")
        state = self._read_state()
        keys = dict(state["keys"])
        existing = keys.get(idempotency_key)
        if existing is not None:
            return MemoryAppendResult(backend_id=str(existing))
        entries = list(state["entries"])
        backend_id = str(len(entries))
        entries.append(text)
        keys[idempotency_key] = backend_id
        self._write_state(
            {"entries": entries, "keys": keys, "compressed": state["compressed"]}
        )
        return MemoryAppendResult(backend_id=backend_id)

    def wake(self, *, part: int = 1, snapshot: int | None = None) -> MemoryWake:
        entries = self.entries
        content = "".join(
            f"#{index} 2026-08-02 {entry}\n" for index, entry in enumerate(entries)
        )
        return MemoryWake(content=content, complete=True, snapshot=len(entries))

    def recall(self, pattern: str) -> str:
        raise NotImplementedError

    def zoom(self, block: str) -> str:
        raise NotImplementedError

    def maintain(
        self,
        compressor: MemoryCompressor,
        *,
        max_compressions: int,
    ) -> MemoryMaintenance:
        state = self._read_state()
        required = max(0, len(state["entries"]) - 1)
        if state["compressed"] or required == 0:
            return MemoryMaintenance(completed_compressions=0, complete=True)
        completed = min(required, max_compressions)
        for index in range(completed):
            compressor.compress(
                MemoryCompressionRequest(
                    block=f"0-{index + 1}",
                    prompt="Synthetic active projection evidence.",
                    max_bytes=280,
                )
            )
        complete = completed == required
        if complete:
            self._write_state(state | {"compressed": True})
        return MemoryMaintenance(
            completed_compressions=completed,
            complete=complete,
            next_request=None
            if complete
            else MemoryCompressionRequest(
                block=f"0-{completed + 1}",
                prompt="Synthetic active projection evidence.",
                max_bytes=280,
            ),
        )

    def invalidate_summary(self, block: str) -> None:
        state = self._read_state()
        self._write_state(state | {"compressed": False})

    @property
    def _state_path(self) -> Path:
        return self._root / "test-store.json"

    def _read_state(self) -> dict[str, object]:
        return dict(json.loads(self._state_path.read_text(encoding="utf-8")))

    def _write_state(self, state: dict[str, object]) -> None:
        self._state_path.write_text(json.dumps(state), encoding="utf-8")


class PaginatedPersistentMemoryStore(PersistentMemoryStore):
    def wake(self, *, part: int = 1, snapshot: int | None = None) -> MemoryWake:
        entries = self.entries
        if part not in {1, 2}:
            raise ValueError("test memory has only two pages")
        if snapshot is not None and snapshot != len(entries):
            raise ValueError("test memory snapshot does not exist")
        selected = entries[:1] if part == 1 else entries[1:]
        content = "".join(
            f"#{index} 2026-08-02 {entry}\n"
            for index, entry in enumerate(selected, start=0 if part == 1 else 1)
        )
        return MemoryWake(
            content=content,
            complete=part == 2,
            snapshot=len(entries),
            next_part=2 if part == 1 else None,
        )


def _engine(tmp_path: Path) -> Engine:
    database = tmp_path / "contx.db"
    upgrade_database(database)
    return create_database_engine(database)


def _seed_source_memories(
    engine: Engine,
    historical: PersistentMemoryStore,
) -> MemoryLink:
    observation = Observation(
        id=OBSERVATION_ID,
        idempotency_key="1" * 64,
        source_type=SourceType.SYNTHETIC,
        captured_at=NOW,
        started_at=NOW,
        ended_at=NOW + timedelta(minutes=10),
        expires_at=NOW + timedelta(hours=48),
        created_at=NOW,
    )
    event = Event(
        id=EVENT_ID,
        idempotency_key="2" * 64,
        lineage_key="3" * 64,
        type=EventType.PROJECT_WORK,
        summary="Synthetic Atlas work.",
        facts={},
        started_at=NOW,
        ended_at=NOW + timedelta(minutes=10),
        valid_from=NOW,
        valid_until=NOW + timedelta(minutes=10),
        epistemic_status=EpistemicStatus.INFERRED,
        confidence=0.8,
        sensitivity=Sensitivity.PERSONAL,
        projects=("Atlas",),
        source_observation_ids=(observation.id,),
        processing_version="session-events-v1",
        created_at=NOW,
        updated_at=NOW,
    )
    historical.initialize()
    old = _candidate(
        candidate_id=OLD_CANDIDATE_ID,
        key="4" * 64,
        text="Atlas deploys to staging.",
        created_at=NOW,
    )
    independent = _candidate(
        candidate_id=INDEPENDENT_CANDIDATE_ID,
        key="5" * 64,
        text="Atlas runtime requires Python 3.12.",
        created_at=NOW + timedelta(seconds=30),
    )
    old_link = _link(
        memory_id=OLD_MEMORY_ID,
        candidate=old,
        backend_id=historical.append(
            old.text,
            idempotency_key=old.idempotency_key,
        ).backend_id,
        created_at=old.created_at,
    )
    independent_link = _link(
        memory_id=INDEPENDENT_MEMORY_ID,
        candidate=independent,
        backend_id=historical.append(
            independent.text,
            idempotency_key=independent.idempotency_key,
        ).backend_id,
        created_at=independent.created_at,
    )
    with session_scope(engine) as database_session:
        repository = PipelineRepository(database_session)
        repository.save_observation(observation)
        repository.save_event(event)
        repository.save_candidate(old)
        repository.save_memory_link(old_link)
        repository.save_candidate(independent)
        repository.save_memory_link(independent_link)
    return old_link


def _seed_additional_memory(
    engine: Engine,
    historical: PersistentMemoryStore,
) -> None:
    candidate = _candidate(
        candidate_id=NEW_CANDIDATE_ID,
        key="6" * 64,
        text="Atlas documentation is maintained in English.",
        created_at=NOW + timedelta(minutes=2),
    )
    link = _link(
        memory_id=NEW_MEMORY_ID,
        candidate=candidate,
        backend_id=historical.append(
            candidate.text,
            idempotency_key=candidate.idempotency_key,
        ).backend_id,
        created_at=candidate.created_at,
    )
    with session_scope(engine) as database_session:
        repository = PipelineRepository(database_session)
        repository.save_candidate(candidate)
        repository.save_memory_link(link)


def _candidate(
    *,
    candidate_id: UUID,
    key: str,
    text: str,
    created_at: datetime,
) -> MemoryCandidate:
    return MemoryCandidate(
        id=candidate_id,
        idempotency_key=key,
        text=text,
        source_type="event",
        source_ids=(EVENT_ID,),
        utility=0.8,
        importance=0.8,
        durability=0.9,
        novelty=0.9,
        confidence=0.8,
        sensitivity=Sensitivity.PERSONAL,
        score=0.8,
        status=CandidateStatus.STORED,
        created_at=created_at,
        processed_at=created_at,
    )


def _link(
    *,
    memory_id: UUID,
    candidate: MemoryCandidate,
    backend_id: str,
    created_at: datetime,
) -> MemoryLink:
    return MemoryLink(
        id=memory_id,
        memory_backend_id=backend_id,
        candidate_id=candidate.id,
        provenance=MemoryProvenance(
            candidate_id=candidate.id,
            event_ids=(EVENT_ID,),
            observation_ids=(OBSERVATION_ID,),
        ),
        confidence=candidate.confidence,
        created_at=created_at,
    )
