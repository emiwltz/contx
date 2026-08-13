"""Promote accepted multi-event candidates into provenance-backed memory."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from contx.db import session_scope
from contx.db.repositories import (
    CandidateDecisionRepository,
    MemoryPromotionBuildRepository,
    MemoryPromotionRepository,
    PatternCandidateRepository,
    PatternRepository,
    PipelineRepository,
)
from contx.errors import ContxError, DatabaseError, MemoryStoreError, PipelineError
from contx.memory_store import MemoryStore
from contx.models import (
    CandidateDecision,
    CandidateDecisionStatus,
    Clock,
    IdentifierSource,
    MemoryCandidate,
    MemoryLink,
    MemoryPromotionBuild,
    MemoryProvenance,
    ProcessingRun,
    ProcessingRunStatus,
)

MEMORY_PROMOTION_VERSION = "memory-promotion-v1"


@dataclass(frozen=True, slots=True)
class MemoryPromotionResult:
    run: ProcessingRun
    build: MemoryPromotionBuild
    memory_links: tuple[MemoryLink, ...]
    memory_maintenance_required: bool


class MemoryPromotionService:
    """Materialize only accepted decisions with complete transitive provenance."""

    def __init__(
        self,
        *,
        engine: Engine,
        memory_store: MemoryStore,
        clock: Clock,
        identifiers: IdentifierSource,
        processing_version: str = MEMORY_PROMOTION_VERSION,
    ) -> None:
        normalized = processing_version.strip()
        if (
            not normalized
            or len(normalized) > 64
            or any(character in normalized for character in "\r\n")
        ):
            raise ValueError("memory promotion version is invalid")
        self._engine = engine
        self._memory_store = memory_store
        self._clock = clock
        self._identifiers = identifiers
        self._processing_version = normalized

    def promote(
        self,
        *,
        source_evaluation_run_id: UUID,
    ) -> MemoryPromotionResult:
        run = ProcessingRun(
            id=self._identifiers.new(),
            pipeline="memory_promotion",
            version=self._processing_version,
            started_at=self._clock.now(),
        )
        build = MemoryPromotionBuild(
            processing_run_id=run.id,
            source_evaluation_run_id=source_evaluation_run_id,
            processing_version=self._processing_version,
        )
        with session_scope(self._engine) as database_session:
            PipelineRepository(database_session).save_processing_run(run)
            MemoryPromotionBuildRepository(database_session).save(build)

        decisions: tuple[CandidateDecision, ...] = ()
        pending_links: list[MemoryLink] = []
        maintenance_required = False
        try:
            self._memory_store.initialize()
            with session_scope(self._engine) as database_session:
                decisions = CandidateDecisionRepository(
                    database_session
                ).decisions_for_processing_run(source_evaluation_run_id)
                accepted = tuple(
                    decision
                    for decision in decisions
                    if decision.status is CandidateDecisionStatus.ACCEPTED
                )
                resolved = tuple(
                    self._resolve_candidate(database_session, decision)
                    for decision in accepted
                )

            for decision, candidate, provenance, existing in resolved:
                if existing is not None:
                    pending_links.append(existing)
                    continue
                appended = self._memory_store.append(
                    candidate.text,
                    idempotency_key=candidate.idempotency_key,
                )
                maintenance_required |= appended.maintenance_required
                pending_links.append(
                    MemoryLink(
                        id=uuid5(
                            NAMESPACE_URL,
                            f"contx:memory-link:{candidate.idempotency_key}",
                        ),
                        memory_backend_id=appended.backend_id,
                        candidate_id=candidate.id,
                        candidate_decision_id=decision.id,
                        provenance=provenance,
                        confidence=candidate.confidence,
                        created_at=self._clock.now(),
                    )
                )

            succeeded = run.succeed(
                ended_at=self._clock.now(),
                input_count=len(decisions),
                output_count=len(pending_links),
            )
            with session_scope(self._engine) as database_session:
                PipelineRepository(database_session).save_processing_run(succeeded)
                repository = MemoryPromotionRepository(database_session)
                persisted_links = tuple(
                    repository.save(link, processing_run_id=succeeded.id)
                    for link in pending_links
                )
        except Exception as error:
            failed = run.fail(
                ended_at=self._clock.now(),
                error_code=_safe_memory_error_code(error),
                input_count=len(decisions),
                output_count=0,
            )
            try:
                with session_scope(self._engine) as database_session:
                    PipelineRepository(database_session).save_processing_run(failed)
            except Exception:
                raise PipelineError(
                    "Memory promotion failed and its status could not be recorded"
                ) from error
            if isinstance(error, ContxError):
                raise
            raise PipelineError("Memory promotion failed") from error

        return MemoryPromotionResult(
            run=succeeded,
            build=build,
            memory_links=persisted_links,
            memory_maintenance_required=maintenance_required,
        )

    def read(self, processing_run_id: UUID) -> MemoryPromotionResult:
        with session_scope(self._engine) as database_session:
            pipeline = PipelineRepository(database_session)
            run = pipeline.processing_run_by_id(processing_run_id)
            build = MemoryPromotionBuildRepository(database_session).by_processing_run(
                processing_run_id
            )
            if (
                run is None
                or build is None
                or run.pipeline != "memory_promotion"
                or run.version != build.processing_version
                or run.status is not ProcessingRunStatus.SUCCEEDED
            ):
                raise PipelineError("Memory promotion run is unavailable")
            links = MemoryPromotionRepository(
                database_session
            ).links_for_processing_run(processing_run_id)
        return MemoryPromotionResult(
            run=run,
            build=build,
            memory_links=links,
            memory_maintenance_required=False,
        )

    def matching_successful(
        self,
        *,
        source_evaluation_run_id: UUID,
    ) -> MemoryPromotionResult | None:
        """Find a successful promotion with the current processing version."""
        with session_scope(self._engine) as database_session:
            builds = MemoryPromotionBuildRepository(
                database_session
            ).successful_for_source(source_evaluation_run_id)
        for build in builds:
            if build.processing_version == self._processing_version:
                return self.read(build.processing_run_id)
        return None

    def _resolve_candidate(
        self,
        database_session: Session,
        decision: CandidateDecision,
    ) -> tuple[
        CandidateDecision,
        MemoryCandidate,
        MemoryProvenance,
        MemoryLink | None,
    ]:
        pipeline = PipelineRepository(database_session)
        candidate = pipeline.candidate_by_id(decision.candidate_id)
        if candidate is None or candidate.source_type != "pattern":
            raise DatabaseError("Accepted memory candidate provenance is invalid")
        if not candidate.sensitivity.permits_durable_memory:
            raise DatabaseError("Sensitive candidate cannot enter durable memory")
        existing = pipeline.memory_link_by_candidate_id(candidate.id)
        patterns = PatternCandidateRepository(database_session).pattern_ids(
            candidate.id
        )
        if set(patterns) != set(candidate.source_ids):
            raise DatabaseError("Candidate pattern provenance is incomplete")
        pattern_repository = PatternRepository(database_session)
        event_ids = tuple(
            dict.fromkeys(
                event_id
                for pattern_id in patterns
                for event_id in pattern_repository.event_ids(pattern_id)
            )
        )
        events = tuple(pipeline.event_by_id(event_id) for event_id in event_ids)
        if not event_ids or any(event is None for event in events):
            raise DatabaseError("Pattern memory event provenance is incomplete")
        observation_ids = tuple(
            dict.fromkeys(
                observation_id
                for event in events
                if event is not None
                for observation_id in event.source_observation_ids
            )
        )
        return (
            decision,
            candidate,
            MemoryProvenance(
                candidate_id=candidate.id,
                pattern_ids=patterns,
                event_ids=event_ids,
                observation_ids=observation_ids,
            ),
            existing,
        )


def _safe_memory_error_code(error: Exception) -> str:
    if isinstance(error, DatabaseError):
        return "database_error"
    if isinstance(error, MemoryStoreError):
        return "memory_store_error"
    if isinstance(error, PipelineError):
        return "pipeline_error"
    return "unexpected_memory_failure"
