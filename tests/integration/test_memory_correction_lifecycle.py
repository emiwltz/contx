"""Append-only memory correction, supersession, and crash recovery tests."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import Engine

from contx.application import MemoryCorrectionService
from contx.db import create_database_engine, session_scope, upgrade_database
from contx.db.repositories import MemoryCorrectionRepository, PipelineRepository
from contx.errors import MemoryStoreError, PipelineError
from contx.memory_store import MemoryAppendResult, RecordingMemoryStore
from contx.models import (
    CandidateStatus,
    EpistemicStatus,
    Event,
    EventType,
    MemoryCandidate,
    MemoryLink,
    MemoryLinkStatus,
    MemoryProvenance,
    Observation,
    Sensitivity,
    SourceType,
)
from tests.helpers import FixedClock

NOW = datetime(2026, 8, 2, 22, 0, tzinfo=UTC)
OBSERVATION_ID = UUID("c0000000-0000-0000-0000-000000000001")
EVENT_ID = UUID("c0000000-0000-0000-0000-000000000002")
CANDIDATE_ID = UUID("c0000000-0000-0000-0000-000000000003")
MEMORY_ID = UUID("c0000000-0000-0000-0000-000000000004")


def test_memory_correction_is_append_only_provenance_backed_and_replayable(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path)
    memory = RecordingMemoryStore()
    composer = RecordingComposer(
        "Correction: Atlas deploys locally; the earlier staging claim is obsolete."
    )
    try:
        original = _seed_memory(engine, memory, sensitivity=Sensitivity.PERSONAL)
        service = MemoryCorrectionService(
            engine=engine,
            memory_store=memory,
            composer=composer,
            clock=FixedClock(NOW + timedelta(minutes=1)),
        )

        corrected = service.correct(
            memory_id=original.id,
            replacement="Atlas deploys locally only.",
        )
        replay = service.correct(
            memory_id=original.id,
            replacement="Atlas deploys locally only.",
        )

        assert corrected.candidate.status is CandidateStatus.STORED
        assert corrected.memory_link.status is MemoryLinkStatus.ACTIVE
        assert corrected.memory_link.supersedes_memory_id == original.id
        assert corrected.memory_link.provenance.pattern_ids == ()
        assert corrected.memory_link.provenance.event_ids == (EVENT_ID,)
        assert corrected.memory_link.provenance.observation_ids == (OBSERVATION_ID,)
        assert corrected.build.candidate_id == corrected.candidate.id
        assert corrected.build.target_memory_id == original.id
        assert corrected.build.model_digest == "synthetic-digest"
        assert corrected.build.replacement_sha256 != ""
        assert replay.memory_link == corrected.memory_link
        assert replay.replayed
        assert len(composer.calls) == 1
        assert memory.entries == (
            "Atlas deploys to staging.",
            (
                "Correction: Atlas deploys locally; the earlier staging claim "
                "is obsolete."
            ),
        )
        with session_scope(engine) as database_session:
            repository = PipelineRepository(database_session)
            persisted_original = repository.memory_link_by_id(original.id)
            assert persisted_original is not None
            assert persisted_original.status is MemoryLinkStatus.SUPERSEDED
            assert (
                repository.memory_link_superseding(original.id) == corrected.memory_link
            )
            assert (
                MemoryCorrectionRepository(database_session).build_by_candidate(
                    corrected.candidate.id
                )
                == corrected.build
            )

        with pytest.raises(PipelineError, match="not active"):
            service.correct(
                memory_id=original.id,
                replacement="Atlas deploys to a different target.",
            )
    finally:
        engine.dispose()


def test_interruption_after_append_leaves_retryable_pending_supersession(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path)
    memory = FailOnceAfterAppendMemory()
    composer = RecordingComposer("Correction: Atlas deploys locally only.")
    try:
        original = _seed_memory(engine, memory, sensitivity=Sensitivity.PERSONAL)
        memory.arm_failure()
        service = MemoryCorrectionService(
            engine=engine,
            memory_store=memory,
            composer=composer,
            clock=FixedClock(NOW + timedelta(minutes=1)),
        )

        with pytest.raises(MemoryStoreError, match="injected boundary failure"):
            service.correct(
                memory_id=original.id,
                replacement="Atlas deploys locally only.",
            )

        with session_scope(engine) as database_session:
            repository = PipelineRepository(database_session)
            pending = repository.memory_link_superseding(original.id)
            persisted_original = repository.memory_link_by_id(original.id)
            assert pending is not None
            assert pending.status is MemoryLinkStatus.PENDING
            assert persisted_original is not None
            assert persisted_original.status is MemoryLinkStatus.ACTIVE

        recovered = service.correct(
            memory_id=original.id,
            replacement="Atlas deploys locally only.",
        )

        assert recovered.memory_link.status is MemoryLinkStatus.ACTIVE
        assert len(memory.entries) == 2
        assert len(composer.calls) == 1
    finally:
        engine.dispose()


def test_memory_corrections_form_one_linear_provenance_preserving_chain(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path)
    memory = RecordingMemoryStore()
    try:
        original = _seed_memory(engine, memory, sensitivity=Sensitivity.PERSONAL)
        first = MemoryCorrectionService(
            engine=engine,
            memory_store=memory,
            composer=RecordingComposer("Correction: Atlas deploys locally only."),
            clock=FixedClock(NOW + timedelta(minutes=1)),
        ).correct(
            memory_id=original.id,
            replacement="Atlas deploys locally only.",
        )
        second_composer = RecordingComposer(
            "Correction: Atlas now deploys to production only."
        )
        second_service = MemoryCorrectionService(
            engine=engine,
            memory_store=memory,
            composer=second_composer,
            clock=FixedClock(NOW + timedelta(minutes=2)),
        )

        second = second_service.correct(
            memory_id=first.memory_link.id,
            replacement="Atlas now deploys to production only.",
        )
        replay = second_service.correct(
            memory_id=first.memory_link.id,
            replacement="Atlas now deploys to production only.",
        )

        assert second.memory_link.supersedes_memory_id == first.memory_link.id
        assert (
            second.memory_link.provenance.pattern_ids
            == first.memory_link.provenance.pattern_ids
        )
        assert (
            second.memory_link.provenance.event_ids
            == first.memory_link.provenance.event_ids
        )
        assert (
            second.memory_link.provenance.observation_ids
            == first.memory_link.provenance.observation_ids
        )
        assert replay.memory_link == second.memory_link
        assert replay.replayed
        assert len(second_composer.calls) == 1
        assert memory.entries == (
            "Atlas deploys to staging.",
            "Correction: Atlas deploys locally only.",
            "Correction: Atlas now deploys to production only.",
        )
        with session_scope(engine) as database_session:
            repository = PipelineRepository(database_session)
            persisted_original = repository.memory_link_by_id(original.id)
            persisted_first = repository.memory_link_by_id(first.memory_link.id)
            persisted_second = repository.memory_link_by_id(second.memory_link.id)
            assert persisted_original is not None
            assert persisted_first is not None
            assert persisted_second is not None
            assert persisted_original.status is MemoryLinkStatus.SUPERSEDED
            assert persisted_first.status is MemoryLinkStatus.SUPERSEDED
            assert persisted_second.status is MemoryLinkStatus.ACTIVE
    finally:
        engine.dispose()


def test_sensitive_memory_cannot_cross_the_correction_boundary(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path)
    memory = RecordingMemoryStore()
    composer = RecordingComposer("Correction: private replacement.")
    try:
        original = _seed_memory(engine, memory, sensitivity=Sensitivity.SENSITIVE)
        service = MemoryCorrectionService(
            engine=engine,
            memory_store=memory,
            composer=composer,
            clock=FixedClock(NOW),
        )

        with pytest.raises(PipelineError, match="Sensitive memory"):
            service.correct(
                memory_id=original.id,
                replacement="private replacement",
            )

        assert composer.calls == []
        assert memory.entries == ("Atlas deploys to staging.",)
    finally:
        engine.dispose()


class RecordingComposer:
    def __init__(self, correction: str) -> None:
        self._correction = correction
        self.calls: list[tuple[str, str, int]] = []

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
        self.calls.append((original, replacement, max_bytes))
        return self._correction


class FailOnceAfterAppendMemory(RecordingMemoryStore):
    def __init__(self) -> None:
        super().__init__()
        self._armed = False

    def arm_failure(self) -> None:
        self._armed = True

    def append(self, text: str, *, idempotency_key: str) -> MemoryAppendResult:
        result = super().append(text, idempotency_key=idempotency_key)
        if self._armed:
            self._armed = False
            raise MemoryStoreError("injected boundary failure")
        return result


def _engine(tmp_path: Path) -> Engine:
    path = tmp_path / "contx.db"
    upgrade_database(path)
    return create_database_engine(path)


def _seed_memory(
    engine: Engine,
    memory: RecordingMemoryStore,
    *,
    sensitivity: Sensitivity,
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
        sensitivity=sensitivity,
        projects=("Atlas",),
        source_observation_ids=(observation.id,),
        processing_version="session-events-v1",
        created_at=NOW,
        updated_at=NOW,
    )
    candidate = MemoryCandidate(
        id=CANDIDATE_ID,
        idempotency_key="4" * 64,
        text="Atlas deploys to staging.",
        source_type="event",
        source_ids=(event.id,),
        utility=0.8,
        importance=0.8,
        durability=0.9,
        novelty=0.9,
        confidence=0.8,
        sensitivity=sensitivity,
        score=0.8,
        status=CandidateStatus.STORED,
        created_at=NOW,
        processed_at=NOW,
    )
    memory.initialize()
    appended = memory.append(candidate.text, idempotency_key=candidate.idempotency_key)
    link = MemoryLink(
        id=MEMORY_ID,
        memory_backend_id=appended.backend_id,
        candidate_id=candidate.id,
        provenance=MemoryProvenance(
            candidate_id=candidate.id,
            event_ids=(event.id,),
            observation_ids=(observation.id,),
        ),
        confidence=candidate.confidence,
        created_at=NOW,
    )
    with session_scope(engine) as database_session:
        repository = PipelineRepository(database_session)
        repository.save_observation(observation)
        repository.save_event(event)
        repository.save_candidate(candidate)
        repository.save_memory_link(link)
    return link
