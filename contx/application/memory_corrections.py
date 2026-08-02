"""Append-only explicit memory corrections with restart-safe supersession."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import Engine

from contx.db import session_scope
from contx.db.repositories import MemoryCorrectionRepository, PipelineRepository
from contx.errors import DatabaseError, PipelineError
from contx.memory_store import MemoryCorrectionComposer, MemoryStore
from contx.models import (
    CandidateStatus,
    Clock,
    MemoryCandidate,
    MemoryCorrectionBuild,
    MemoryLink,
    MemoryLinkStatus,
    MemoryProvenance,
)
from contx.models.common import build_idempotency_key

MEMORY_CORRECTION_VERSION = "memory-correction-v1"
DEFAULT_MEMORY_ENTRY_BYTES = 280


@dataclass(frozen=True, slots=True)
class MemoryCorrectionResult:
    candidate: MemoryCandidate
    memory_link: MemoryLink
    build: MemoryCorrectionBuild
    superseded_memory_id: UUID
    replayed: bool
    maintenance_required: bool


class MemoryCorrectionService:
    """Stage, append, and atomically finalize one explicit replacement."""

    def __init__(
        self,
        *,
        engine: Engine,
        memory_store: MemoryStore,
        composer: MemoryCorrectionComposer,
        clock: Clock,
        max_memory_bytes: int = DEFAULT_MEMORY_ENTRY_BYTES,
    ) -> None:
        if not 64 <= max_memory_bytes <= 4096:
            raise ValueError("memory correction byte limit is invalid")
        self._engine = engine
        self._memory_store = memory_store
        self._composer = composer
        self._clock = clock
        self._max_memory_bytes = max_memory_bytes

    def correct(
        self,
        *,
        memory_id: UUID,
        replacement: str,
    ) -> MemoryCorrectionResult:
        normalized_replacement = _validate_replacement(replacement)
        request_key = build_idempotency_key(
            MEMORY_CORRECTION_VERSION,
            memory_id,
            normalized_replacement,
        )
        candidate_id = uuid5(
            NAMESPACE_URL,
            f"contx:memory-correction-candidate:{request_key}",
        )
        memory_link_id = uuid5(
            NAMESPACE_URL,
            f"contx:memory-correction-link:{request_key}",
        )

        with session_scope(self._engine) as database_session:
            pipeline = PipelineRepository(database_session)
            correction_repository = MemoryCorrectionRepository(database_session)
            existing_candidate = pipeline.candidate_by_id(candidate_id)
            existing_link = pipeline.memory_link_by_candidate_id(candidate_id)
            if existing_candidate is not None or existing_link is not None:
                if existing_candidate is None or existing_link is None:
                    raise DatabaseError("Memory correction staging is incomplete")
                build = correction_repository.build_by_candidate(candidate_id)
                if build is None:
                    raise DatabaseError("Memory correction model provenance is missing")
                if (
                    existing_link.id != memory_link_id
                    or existing_link.supersedes_memory_id != memory_id
                ):
                    raise DatabaseError("Memory correction replay identity conflicts")
                if existing_link.status is MemoryLinkStatus.ACTIVE:
                    return MemoryCorrectionResult(
                        candidate=existing_candidate,
                        memory_link=existing_link,
                        build=build,
                        superseded_memory_id=memory_id,
                        replayed=True,
                        maintenance_required=False,
                    )
                if existing_link.status is not MemoryLinkStatus.PENDING:
                    raise DatabaseError("Memory correction replay state is invalid")
                candidate = existing_candidate
                pending_link = existing_link
            else:
                target = pipeline.memory_link_by_id(memory_id)
                if target is None:
                    raise PipelineError("Memory correction target was not found")
                if target.status is not MemoryLinkStatus.ACTIVE:
                    raise PipelineError("Memory correction target is not active")
                successor = pipeline.memory_link_superseding(memory_id)
                if successor is not None:
                    raise PipelineError(
                        "Memory correction target already has a successor"
                    )
                original = pipeline.candidate_by_id(target.candidate_id)
                if original is None:
                    raise DatabaseError("Memory correction source is unavailable")
                if not original.sensitivity.permits_durable_memory:
                    raise PipelineError("Sensitive memory cannot be corrected durably")
                source = (target, original)

        if existing_candidate is None:
            target, original = source
            started_at = self._clock.now()
            composed = self._composer.compose(
                original=original.text,
                replacement=normalized_replacement,
                max_bytes=self._max_memory_bytes,
            )
            correction_text = _validate_composed_correction(
                composed,
                max_bytes=self._max_memory_bytes,
            )
            ended_at = self._clock.now()
            model_digest = self._composer.model_digest
            if model_digest is None:
                raise PipelineError("Local correction model identity is unavailable")
            wall_duration_ms = max(
                0,
                int((ended_at - started_at).total_seconds() * 1000),
            )
            candidate = MemoryCandidate(
                id=candidate_id,
                idempotency_key=request_key,
                text=correction_text,
                source_type="memory_correction",
                source_ids=(target.id,),
                utility=max(original.utility, 0.8),
                importance=original.importance,
                durability=original.durability,
                novelty=1.0,
                recurrence=original.recurrence,
                confidence=1.0,
                ambiguity=0.0,
                redundancy=0.0,
                sensitivity=original.sensitivity,
                score=1.0,
                scoring_version=MEMORY_CORRECTION_VERSION,
                status=CandidateStatus.ACCEPTED,
                created_at=ended_at,
                processed_at=ended_at,
            )
            pending_link = MemoryLink(
                id=memory_link_id,
                memory_backend_id=f"pending:{memory_link_id}",
                candidate_id=candidate.id,
                provenance=MemoryProvenance(
                    candidate_id=candidate.id,
                    pattern_ids=target.provenance.pattern_ids,
                    event_ids=target.provenance.event_ids,
                    observation_ids=target.provenance.observation_ids,
                ),
                confidence=candidate.confidence,
                status=MemoryLinkStatus.PENDING,
                supersedes_memory_id=target.id,
                created_at=ended_at,
            )
            build = MemoryCorrectionBuild(
                candidate_id=candidate.id,
                target_memory_id=target.id,
                provider=self._composer.provider,
                endpoint=self._composer.endpoint,
                model=self._composer.model,
                model_digest=model_digest,
                prompt_version=self._composer.prompt_version,
                output_schema_version=self._composer.output_schema_version,
                replacement_sha256=hashlib.sha256(
                    normalized_replacement.encode("utf-8")
                ).hexdigest(),
                started_at=started_at,
                ended_at=ended_at,
                wall_duration_ms=wall_duration_ms,
            )
            with session_scope(self._engine) as database_session:
                candidate, pending_link, build = MemoryCorrectionRepository(
                    database_session
                ).stage(candidate=candidate, link=pending_link, build=build)

        if build is None:
            raise DatabaseError("Memory correction model provenance is missing")
        self._memory_store.initialize()
        appended = self._memory_store.append(
            candidate.text,
            idempotency_key=candidate.idempotency_key,
        )
        with session_scope(self._engine) as database_session:
            stored_candidate, active_link = MemoryCorrectionRepository(
                database_session
            ).finalize(
                candidate_id=candidate.id,
                memory_link_id=pending_link.id,
                backend_id=appended.backend_id,
                processed_at=self._clock.now(),
            )
        return MemoryCorrectionResult(
            candidate=stored_candidate,
            memory_link=active_link,
            build=build,
            superseded_memory_id=memory_id,
            replayed=False,
            maintenance_required=appended.maintenance_required,
        )


def _validate_replacement(value: str) -> str:
    normalized = value.strip()
    if (
        not normalized
        or len(normalized) > 4000
        or "\n" in normalized
        or "\r" in normalized
    ):
        raise PipelineError("Memory replacement must be one bounded non-empty line")
    return normalized


def _validate_composed_correction(value: str, *, max_bytes: int) -> str:
    normalized = value.strip()
    if (
        not normalized
        or "\n" in normalized
        or "\r" in normalized
        or not normalized.lower().startswith("correction:")
    ):
        raise PipelineError("Local model did not produce an explicit correction")
    if len(normalized.encode("utf-8")) > max_bytes:
        raise PipelineError("Local model correction exceeded the memory byte limit")
    return normalized
