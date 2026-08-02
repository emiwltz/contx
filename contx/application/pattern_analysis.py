"""Replayable pattern, candidate, and evaluation application services."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import Engine

from contx.application.activity_timeline import ActivityTimelineService
from contx.candidates import PatternCandidateProducer
from contx.db import session_scope
from contx.db.repositories import (
    CandidateBuildRepository,
    CandidateDecisionRepository,
    CandidateEvaluationBuildRepository,
    PatternBuildRepository,
    PatternCandidateRepository,
    PatternRepository,
    PipelineRepository,
)
from contx.errors import ContxError, DatabaseError, PipelineError
from contx.memory_worker import TransparentCandidateWorker
from contx.models import (
    CandidateBuild,
    CandidateDecision,
    CandidateEvaluationBuild,
    Clock,
    IdentifierSource,
    MemoryCandidate,
    Pattern,
    PatternBuild,
    ProcessingRun,
    ProcessingRunStatus,
)
from contx.patterns import PatternEngine


@dataclass(frozen=True, slots=True)
class PatternAnalysisResult:
    run: ProcessingRun
    build: PatternBuild
    patterns: tuple[Pattern, ...]


@dataclass(frozen=True, slots=True)
class PatternCandidateResult:
    run: ProcessingRun
    build: CandidateBuild
    candidates: tuple[MemoryCandidate, ...]


@dataclass(frozen=True, slots=True)
class CandidateEvaluationResult:
    run: ProcessingRun
    build: CandidateEvaluationBuild
    decisions: tuple[CandidateDecision, ...]


class PatternAnalysisService:
    """Detect and persist patterns from one immutable activity timeline."""

    def __init__(
        self,
        *,
        engine: Engine,
        timeline_service: ActivityTimelineService,
        pattern_engine: PatternEngine,
        clock: Clock,
        identifiers: IdentifierSource,
    ) -> None:
        self._engine = engine
        self._timeline_service = timeline_service
        self._pattern_engine = pattern_engine
        self._clock = clock
        self._identifiers = identifiers

    def build(
        self,
        *,
        source_timeline_run_id: UUID,
        comparison_boundary: datetime,
    ) -> PatternAnalysisResult:
        run = ProcessingRun(
            id=self._identifiers.new(),
            pipeline="patterns",
            version=self._pattern_engine.processing_version,
            started_at=self._clock.now(),
        )
        build = PatternBuild(
            processing_run_id=run.id,
            source_timeline_run_id=source_timeline_run_id,
            processing_version=self._pattern_engine.processing_version,
            comparison_boundary=comparison_boundary,
            min_project_events=self._pattern_engine.min_project_events,
            resumption_gap_seconds=int(
                self._pattern_engine.resumption_gap.total_seconds()
            ),
            change_ratio=self._pattern_engine.change_ratio,
        )
        self._save_run_and_build(run, build)
        patterns: tuple[Pattern, ...] = ()
        try:
            timeline = self._timeline_service.read(source_timeline_run_id)
            patterns = self._pattern_engine.detect(
                timeline,
                comparison_boundary=comparison_boundary,
            )
            succeeded = run.succeed(
                ended_at=self._clock.now(),
                input_count=len(timeline.entries),
                output_count=len(patterns),
            )
            with session_scope(self._engine) as database_session:
                PipelineRepository(database_session).save_processing_run(succeeded)
                repository = PatternRepository(database_session)
                for pattern in patterns:
                    repository.save(pattern, processing_run_id=succeeded.id)
        except Exception as error:
            self._record_failure(run, error, input_count=0)
            if isinstance(error, ContxError):
                raise
            raise PipelineError("Pattern analysis failed") from error
        return self.read(succeeded.id)

    def read(self, processing_run_id: UUID) -> PatternAnalysisResult:
        with session_scope(self._engine) as database_session:
            pipeline = PipelineRepository(database_session)
            run = pipeline.processing_run_by_id(processing_run_id)
            build = PatternBuildRepository(database_session).by_processing_run(
                processing_run_id
            )
            if (
                run is None
                or build is None
                or run.pipeline != "patterns"
                or run.version != build.processing_version
                or run.status is not ProcessingRunStatus.SUCCEEDED
            ):
                raise PipelineError("Pattern processing run is unavailable")
            patterns = PatternRepository(database_session).patterns_for_processing_run(
                processing_run_id
            )
        return PatternAnalysisResult(run=run, build=build, patterns=patterns)

    def _save_run_and_build(self, run: ProcessingRun, build: PatternBuild) -> None:
        with session_scope(self._engine) as database_session:
            PipelineRepository(database_session).save_processing_run(run)
            PatternBuildRepository(database_session).save(build)

    def _record_failure(
        self,
        run: ProcessingRun,
        error: Exception,
        *,
        input_count: int,
    ) -> None:
        failed = run.fail(
            ended_at=self._clock.now(),
            error_code=_safe_analysis_error_code(error),
            input_count=input_count,
            output_count=0,
        )
        try:
            with session_scope(self._engine) as database_session:
                PipelineRepository(database_session).save_processing_run(failed)
        except Exception:
            raise PipelineError(
                "Pattern analysis failed and its status could not be recorded"
            ) from error


class PatternCandidateService:
    """Build replayable, fused candidates from persisted patterns."""

    def __init__(
        self,
        *,
        engine: Engine,
        producer: PatternCandidateProducer,
        clock: Clock,
        identifiers: IdentifierSource,
    ) -> None:
        self._engine = engine
        self._producer = producer
        self._clock = clock
        self._identifiers = identifiers

    def build(self, *, source_pattern_run_id: UUID) -> PatternCandidateResult:
        run = ProcessingRun(
            id=self._identifiers.new(),
            pipeline="pattern_candidates",
            version=self._producer.processing_version,
            started_at=self._clock.now(),
        )
        build = CandidateBuild(
            processing_run_id=run.id,
            source_pattern_run_id=source_pattern_run_id,
            processing_version=self._producer.processing_version,
            scoring_weights=self._producer.scoring_weights,
        )
        with session_scope(self._engine) as database_session:
            PipelineRepository(database_session).save_processing_run(run)
            CandidateBuildRepository(database_session).save(build)
        try:
            with session_scope(self._engine) as database_session:
                patterns = PatternRepository(
                    database_session
                ).patterns_for_processing_run(source_pattern_run_id)
            candidates = self._producer.produce(patterns)
            succeeded = run.succeed(
                ended_at=self._clock.now(),
                input_count=len(patterns),
                output_count=len(candidates),
            )
            with session_scope(self._engine) as database_session:
                PipelineRepository(database_session).save_processing_run(succeeded)
                repository = PatternCandidateRepository(database_session)
                for candidate in candidates:
                    repository.save(candidate, processing_run_id=succeeded.id)
        except Exception as error:
            _record_run_failure(self._engine, self._clock, run, error)
            if isinstance(error, ContxError):
                raise
            raise PipelineError("Pattern candidate build failed") from error
        return self.read(succeeded.id)

    def read(self, processing_run_id: UUID) -> PatternCandidateResult:
        with session_scope(self._engine) as database_session:
            pipeline = PipelineRepository(database_session)
            run = pipeline.processing_run_by_id(processing_run_id)
            build = CandidateBuildRepository(database_session).by_processing_run(
                processing_run_id
            )
            if (
                run is None
                or build is None
                or run.pipeline != "pattern_candidates"
                or run.version != build.processing_version
                or run.status is not ProcessingRunStatus.SUCCEEDED
            ):
                raise PipelineError("Pattern candidate processing run is unavailable")
            candidates = PatternCandidateRepository(
                database_session
            ).candidates_for_processing_run(processing_run_id)
        return PatternCandidateResult(run=run, build=build, candidates=candidates)


class CandidateEvaluationService:
    """Evaluate a frozen candidate run under one explicit replayable policy."""

    def __init__(
        self,
        *,
        engine: Engine,
        worker: TransparentCandidateWorker,
        clock: Clock,
        identifiers: IdentifierSource,
    ) -> None:
        self._engine = engine
        self._worker = worker
        self._clock = clock
        self._identifiers = identifiers

    def evaluate(
        self,
        *,
        source_candidate_run_id: UUID,
    ) -> CandidateEvaluationResult:
        run = ProcessingRun(
            id=self._identifiers.new(),
            pipeline="candidate_evaluation",
            version=self._worker.policy_version,
            started_at=self._clock.now(),
        )
        build = CandidateEvaluationBuild(
            processing_run_id=run.id,
            source_candidate_run_id=source_candidate_run_id,
            policy_version=self._worker.policy_version,
            acceptance_threshold=self._worker.acceptance_threshold,
            minimum_confidence=self._worker.minimum_confidence,
            maximum_ambiguity=self._worker.maximum_ambiguity,
            maximum_redundancy=self._worker.maximum_redundancy,
        )
        with session_scope(self._engine) as database_session:
            PipelineRepository(database_session).save_processing_run(run)
            CandidateEvaluationBuildRepository(database_session).save(build)
        try:
            with session_scope(self._engine) as database_session:
                candidates = PatternCandidateRepository(
                    database_session
                ).candidates_for_processing_run(source_candidate_run_id)
            decisions = self._worker.evaluate(
                candidates,
                processing_run_id=run.id,
            )
            succeeded = run.succeed(
                ended_at=self._clock.now(),
                input_count=len(candidates),
                output_count=len(decisions),
            )
            with session_scope(self._engine) as database_session:
                PipelineRepository(database_session).save_processing_run(succeeded)
                repository = CandidateDecisionRepository(database_session)
                for decision in decisions:
                    repository.save(decision)
        except Exception as error:
            _record_run_failure(self._engine, self._clock, run, error)
            if isinstance(error, ContxError):
                raise
            raise PipelineError("Candidate evaluation failed") from error
        return self.read(succeeded.id)

    def read(self, processing_run_id: UUID) -> CandidateEvaluationResult:
        with session_scope(self._engine) as database_session:
            pipeline = PipelineRepository(database_session)
            run = pipeline.processing_run_by_id(processing_run_id)
            build = CandidateEvaluationBuildRepository(
                database_session
            ).by_processing_run(processing_run_id)
            if (
                run is None
                or build is None
                or run.pipeline != "candidate_evaluation"
                or run.version != build.policy_version
                or run.status is not ProcessingRunStatus.SUCCEEDED
            ):
                raise PipelineError("Candidate evaluation run is unavailable")
            decisions = CandidateDecisionRepository(
                database_session
            ).decisions_for_processing_run(processing_run_id)
        return CandidateEvaluationResult(run=run, build=build, decisions=decisions)


def _record_run_failure(
    engine: Engine,
    clock: Clock,
    run: ProcessingRun,
    error: Exception,
) -> None:
    failed = run.fail(
        ended_at=clock.now(),
        error_code=_safe_analysis_error_code(error),
    )
    try:
        with session_scope(engine) as database_session:
            PipelineRepository(database_session).save_processing_run(failed)
    except Exception:
        raise PipelineError(
            "Analysis stage failed and its status could not be recorded"
        ) from error


def _safe_analysis_error_code(error: Exception) -> str:
    if isinstance(error, DatabaseError):
        return "database_error"
    if isinstance(error, PipelineError):
        return "pipeline_error"
    return "unexpected_analysis_failure"
