"""Restartable real-service refresh from synthetic screenshots to active memory."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import Engine

from contx.application import (
    ActiveMemoryProjectionService,
    ActivityTimelineService,
    CandidateEvaluationService,
    ContextRefreshService,
    LocalModelEventService,
    LocalModelProcessingService,
    MemoryMaintenanceService,
    MemoryPromotionService,
    PatternAnalysisService,
    PatternCandidateService,
    aligned_refresh_window,
)
from contx.candidates import PatternCandidateProducer
from contx.db import create_database_engine, session_scope, upgrade_database
from contx.db.repositories import PipelineRepository
from contx.errors import PipelineError
from contx.events import (
    ModelActivitySessionizer,
    ModelTransformationEventBuilder,
    SessionizedModelEventBuilder,
)
from contx.memory_store import (
    MemoryCompressionRequest,
    RecordingMemoryStore,
)
from contx.memory_worker import TransparentCandidateWorker
from contx.model_provider import (
    LocalModelExecution,
    LocalModelRequest,
    LocalModelRuntimeStatus,
    ModelInterpretation,
)
from contx.models import Observation, Sensitivity, SourceType, UuidIdentifierSource
from contx.patterns import TemporalPatternEngine
from contx.raw_store import FilesystemRawStore
from tests.helpers import FixedClock

START = datetime(2026, 8, 10, 8, tzinfo=UTC)
BOUNDARY = START + timedelta(days=1)
END = START + timedelta(days=2)
NOW = END - timedelta(hours=1)
MODEL = "gemma4:e4b-it-qat"
DIGEST = f"sha256:{'a' * 64}"
OBSERVATION_IDS = tuple(UUID(int=1000 + index) for index in range(3))
CAPTURED_AT = (
    START + timedelta(hours=1),
    START + timedelta(days=1, hours=2),
    START + timedelta(days=1, hours=5),
)


class SyntheticModelProvider:
    def __init__(self) -> None:
        self.requests: list[LocalModelRequest] = []

    def status(self) -> LocalModelRuntimeStatus:
        return LocalModelRuntimeStatus(
            endpoint="http://127.0.0.1:11434",
            runtime_available=True,
            runtime_version="0.32.5",
            model=MODEL,
            model_available=True,
            model_digest=DIGEST,
        )

    def interpret(self, request: LocalModelRequest) -> LocalModelExecution:
        self.requests.append(request)
        return LocalModelExecution(
            request_id=request.id,
            source_observation_ids=request.source_observation_ids,
            endpoint="http://127.0.0.1:11434",
            runtime_version="0.32.5",
            model=MODEL,
            model_digest=DIGEST,
            image_sha256=request.image_sha256,
            interpretation=ModelInterpretation(
                summary="Working on the CONTX implementation.",
                activity_type="coding",
                observed_facts=("A local editor contains CONTX source code.",),
                inferred_context=("The CONTX v0 implementation is active.",),
                projects=("CONTX",),
                entities=(),
                sensitivity=Sensitivity.PERSONAL,
                sensitive_categories=(),
                confidence=0.9,
                memory_relevance=0.9,
            ),
            started_at=NOW,
            ended_at=NOW,
            wall_duration_ms=0,
        )


class StaticCompressor:
    def compress(self, request: MemoryCompressionRequest) -> str:
        return "Synthetic CONTX summary."


class FailFirstPatternBuild:
    def __init__(self, wrapped: PatternAnalysisService) -> None:
        self._wrapped = wrapped
        self._failed = False

    def build(self, **kwargs: object) -> object:
        if not self._failed:
            self._failed = True
            raise PipelineError("synthetic interrupted derivation")
        return self._wrapped.build(**kwargs)  # type: ignore[arg-type]

    def matching_successful(self, **kwargs: object) -> object:
        return self._wrapped.matching_successful(**kwargs)  # type: ignore[arg-type]


def test_refresh_defers_until_bounded_queues_are_drained_then_publishes(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path)
    raw_store = _seed_screenshots(engine, tmp_path)
    provider = SyntheticModelProvider()
    historical = RecordingMemoryStore()
    service = _service(
        engine,
        tmp_path,
        raw_store=raw_store,
        provider=provider,
        historical=historical,
        model_batch_size=1,
    )
    try:
        first = service.run_once(
            window_start=START,
            window_end=END,
            comparison_boundary=BOUNDARY,
        )
        second = service.run_once(
            window_start=START,
            window_end=END,
            comparison_boundary=BOUNDARY,
        )

        assert first.deferred and not first.blocked and not first.completed
        assert first.model_processing.backlog_count == 2
        assert first.timeline is None
        assert second.deferred and not second.completed
        assert second.model_processing.backlog_count == 1
        with session_scope(engine) as database_session:
            assert not any(
                run.pipeline == "activity_timeline"
                for run in PipelineRepository(database_session).processing_runs()
            )

        completed = service.run_once(
            window_start=START,
            window_end=END,
            comparison_boundary=BOUNDARY,
        )

        assert completed.completed and not completed.blocked and not completed.deferred
        assert completed.model_processing.backlog_count == 0
        assert completed.model_events.backlog_count == 0
        assert completed.timeline is not None
        assert len(completed.timeline.timeline.entries) == 3
        assert completed.patterns is not None
        assert len(completed.patterns.patterns) >= 2
        assert completed.candidates is not None
        assert len(completed.candidates.candidates) == 1
        assert completed.evaluation is not None
        assert len(completed.evaluation.decisions) == 1
        assert completed.promotion is not None
        assert len(completed.promotion.memory_links) == 1
        assert completed.maintenance is not None
        assert completed.maintenance.maintenance.complete
        assert completed.projection is not None
        assert completed.projection.active_memory_count == 1
        assert len(provider.requests) == 3
        assert len(historical.entries) == 1

        replay = service.run_once(
            window_start=START,
            window_end=END,
            comparison_boundary=BOUNDARY,
            reuse_completed_derivation=True,
        )

        assert replay.completed
        assert replay.reused_derivation
        assert replay.timeline is not None and replay.timeline.reused
        assert replay.timeline.run.id == completed.timeline.run.id
        assert replay.promotion is not None
        assert replay.promotion.run.id == completed.promotion.run.id
        assert replay.promotion.memory_links == completed.promotion.memory_links
        assert len(provider.requests) == 3
        assert len(historical.entries) == 1
    finally:
        engine.dispose()


def test_refresh_resumes_after_timeline_publication_without_skipping_derivation(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path)
    raw_store = _seed_screenshots(engine, tmp_path)
    provider = SyntheticModelProvider()
    service = _service(
        engine,
        tmp_path,
        raw_store=raw_store,
        provider=provider,
        historical=RecordingMemoryStore(),
        model_batch_size=10,
        fail_first_pattern=True,
    )
    try:
        with pytest.raises(PipelineError, match="interrupted derivation"):
            service.run_once(
                window_start=START,
                window_end=END,
                comparison_boundary=BOUNDARY,
                reuse_completed_derivation=True,
            )

        resumed = service.run_once(
            window_start=START,
            window_end=END,
            comparison_boundary=BOUNDARY,
            reuse_completed_derivation=True,
        )

        assert resumed.completed
        assert resumed.timeline is not None and resumed.timeline.reused
        assert not resumed.reused_derivation
        assert resumed.patterns is not None
        assert resumed.promotion is not None
        assert len(resumed.promotion.memory_links) == 1
    finally:
        engine.dispose()


def test_refresh_rejects_an_invalid_window_before_invoking_the_model(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path)
    raw_store = _seed_screenshots(engine, tmp_path)
    provider = SyntheticModelProvider()
    service = _service(
        engine,
        tmp_path,
        raw_store=raw_store,
        provider=provider,
        historical=RecordingMemoryStore(),
        model_batch_size=10,
    )
    try:
        with pytest.raises(PipelineError, match="start < comparison boundary < end"):
            service.run_once(
                window_start=START,
                window_end=END,
                comparison_boundary=START,
            )

        assert provider.requests == []
        with session_scope(engine) as database_session:
            assert PipelineRepository(database_session).processing_runs() == ()
    finally:
        engine.dispose()


def test_periodic_window_is_stable_inside_one_analysis_interval() -> None:
    first = aligned_refresh_window(
        at=datetime(2026, 8, 13, 9, 1, tzinfo=UTC),
        analysis_interval=timedelta(hours=2),
        analysis_window=timedelta(days=14),
        comparison_period=timedelta(days=7),
    )
    second = aligned_refresh_window(
        at=datetime(2026, 8, 13, 9, 59, tzinfo=UTC),
        analysis_interval=timedelta(hours=2),
        analysis_window=timedelta(days=14),
        comparison_period=timedelta(days=7),
    )

    assert first == second
    assert first.end == datetime(2026, 8, 13, 8, tzinfo=UTC)
    assert first.start == datetime(2026, 7, 30, 8, tzinfo=UTC)
    assert first.comparison_boundary == datetime(2026, 8, 6, 8, tzinfo=UTC)


def _service(
    engine: Engine,
    tmp_path: Path,
    *,
    raw_store: FilesystemRawStore,
    provider: SyntheticModelProvider,
    historical: RecordingMemoryStore,
    model_batch_size: int,
    fail_first_pattern: bool = False,
) -> ContextRefreshService:
    clock = FixedClock(NOW)
    identifiers = UuidIdentifierSource()
    timeline = ActivityTimelineService(
        engine=engine,
        sessionizer=ModelActivitySessionizer(),
        builder=SessionizedModelEventBuilder(clock=clock),
        clock=clock,
        identifiers=identifiers,
    )
    patterns = PatternAnalysisService(
        engine=engine,
        timeline_service=timeline,
        pattern_engine=TemporalPatternEngine(clock=clock),
        clock=clock,
        identifiers=identifiers,
    )
    return ContextRefreshService(
        model_processing=LocalModelProcessingService(
            engine=engine,
            raw_store=raw_store,
            provider=provider,
            endpoint="http://127.0.0.1:11434",
            configured_model=MODEL,
            max_image_bytes=1024,
            clock=clock,
            identifiers=identifiers,
            batch_size=model_batch_size,
        ),
        model_events=LocalModelEventService(
            engine=engine,
            builder=ModelTransformationEventBuilder(clock=clock),
            clock=clock,
            identifiers=identifiers,
        ),
        timeline=timeline,
        patterns=(FailFirstPatternBuild(patterns) if fail_first_pattern else patterns),  # type: ignore[arg-type]
        candidates=PatternCandidateService(
            engine=engine,
            producer=PatternCandidateProducer(clock=clock),
            clock=clock,
            identifiers=identifiers,
        ),
        evaluation=CandidateEvaluationService(
            engine=engine,
            worker=TransparentCandidateWorker(clock=clock),
            clock=clock,
            identifiers=identifiers,
        ),
        promotion=MemoryPromotionService(
            engine=engine,
            memory_store=historical,
            clock=clock,
            identifiers=identifiers,
        ),
        maintenance=MemoryMaintenanceService(
            engine=engine,
            memory_store=historical,
            compressor=StaticCompressor(),
            clock=clock,
            identifiers=identifiers,
            max_compressions=1,
        ),
        projection=ActiveMemoryProjectionService(
            engine=engine,
            projection_root=tmp_path / "active-memory",
            memory_store_factory=lambda _path: RecordingMemoryStore(),
            compressor=StaticCompressor(),
        ),
    )


def _seed_screenshots(engine: Engine, tmp_path: Path) -> FilesystemRawStore:
    store = FilesystemRawStore(tmp_path / "raw", disk_budget_bytes=1024 * 1024)
    observations: list[Observation] = []
    for index, (observation_id, captured_at) in enumerate(
        zip(OBSERVATION_IDS, CAPTURED_AT, strict=True)
    ):
        artifact = store.write(
            b"\x89PNG\r\n\x1a\n" + bytes((index,)),
            artifact_id=observation_id,
            suffix=".png",
            captured_at=captured_at,
            retention=timedelta(hours=48),
        )
        observations.append(
            Observation(
                id=observation_id,
                idempotency_key=f"{index + 1:x}" * 64,
                source_type=SourceType.SCREENSHOT,
                captured_at=captured_at,
                started_at=captured_at,
                ended_at=captured_at + timedelta(minutes=30),
                app_name="Synthetic Editor",
                app_bundle_id="com.example.editor",
                window_title="CONTX implementation",
                artifact_path=str(artifact.path),
                content_hash=artifact.content_hash,
                expires_at=artifact.expires_at,
                created_at=captured_at,
            )
        )
    with session_scope(engine) as database_session:
        repository = PipelineRepository(database_session)
        for observation in observations:
            repository.save_observation(observation)
    return store


def _engine(tmp_path: Path) -> Engine:
    database_path = tmp_path / "contx.db"
    upgrade_database(database_path)
    return create_database_engine(database_path)
