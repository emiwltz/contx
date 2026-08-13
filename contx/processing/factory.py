"""Validated composition root for periodic local semantic processing."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta

from sqlalchemy import Engine

from contx.application.activity_timeline import ActivityTimelineService
from contx.application.context_refresh import (
    ContextRefreshResult,
    ContextRefreshService,
    aligned_refresh_window,
)
from contx.application.local_model_events import LocalModelEventService
from contx.application.local_model_processing import LocalModelProcessingService
from contx.application.memory_lifecycle import MemoryPromotionService
from contx.application.memory_maintenance import MemoryMaintenanceService
from contx.application.memory_projection import ActiveMemoryProjectionService
from contx.application.pattern_analysis import (
    CandidateEvaluationService,
    PatternAnalysisService,
    PatternCandidateService,
)
from contx.candidates import PatternCandidateProducer
from contx.daemon import DaemonLease
from contx.db import create_database_engine, upgrade_database
from contx.errors import ConfigurationError
from contx.events import (
    ModelActivitySessionizer,
    ModelTransformationEventBuilder,
    SessionizedModelEventBuilder,
)
from contx.memory_store import (
    OllamaMemoryCompressor,
    OptMemAdapter,
    resolve_optmem_executable,
)
from contx.memory_worker import TransparentCandidateWorker
from contx.model_provider import OllamaModelProvider
from contx.models import Clock, SystemClock, UuidIdentifierSource
from contx.patterns import TemporalPatternEngine
from contx.raw_store import FilesystemRawStore
from contx.settings import (
    RuntimePaths,
    initialize_runtime_paths,
    load_settings,
    resolve_runtime_paths,
)


class ConfiguredContextProcessor:
    """Own one periodic refresh attempt and its database resources."""

    def __init__(
        self,
        *,
        service: ContextRefreshService,
        engine: Engine,
        clock: Clock,
        analysis_interval: timedelta,
        analysis_window: timedelta,
        comparison_period: timedelta,
        lease: DaemonLease,
    ) -> None:
        self._service = service
        self._engine = engine
        self._clock = clock
        self._analysis_interval = analysis_interval
        self._analysis_window = analysis_window
        self._comparison_period = comparison_period
        self._lease = lease
        self._closed = False

    def run(self) -> ContextRefreshResult:
        if self._closed:
            raise RuntimeError("configured context processor is closed")
        window = aligned_refresh_window(
            at=self._clock.now(),
            analysis_interval=self._analysis_interval,
            analysis_window=self._analysis_window,
            comparison_period=self._comparison_period,
        )
        return self._service.run_once(
            window_start=window.start,
            window_end=window.end,
            comparison_boundary=window.comparison_boundary,
            reuse_completed_derivation=True,
        )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._engine.dispose()
        finally:
            self._lease.release()

    def __enter__(self) -> ConfiguredContextProcessor:
        return self

    def __exit__(self, *_error: object) -> None:
        self.close()


def build_context_processor(
    *,
    paths: RuntimePaths | None = None,
    environ: Mapping[str, str] | None = None,
    clock: Clock | None = None,
) -> ConfiguredContextProcessor:
    """Build but do not run one background-gated local processing attempt."""
    runtime_paths = paths or resolve_runtime_paths(environ)
    initialize_runtime_paths(runtime_paths)
    settings = load_settings(runtime_paths, environ)
    if not settings.collection.background_collection_enabled:
        raise ConfigurationError(
            "Background processing is disabled with background collection"
        )
    lease = DaemonLease(
        runtime_paths.processor_lock,
        owner="context processor",
    )
    lease.acquire()
    engine: Engine | None = None
    try:
        upgrade_database(runtime_paths.database_file)
        engine = create_database_engine(runtime_paths.database_file)
        processor_clock = clock or SystemClock()
        identifiers = UuidIdentifierSource()
        raw_store = FilesystemRawStore(
            runtime_paths.raw,
            disk_budget_bytes=settings.collection.raw_disk_budget_mb * 1024 * 1024,
        )
        provider = OllamaModelProvider(
            model=settings.model.model_name,
            endpoint=settings.model.endpoint,
            timeout_seconds=settings.model.timeout_seconds,
            keep_alive=settings.model.keep_alive,
            context_tokens=settings.model.context_tokens,
            max_output_tokens=settings.model.max_output_tokens,
            max_image_bytes=settings.model.max_image_mb * 1024 * 1024,
            max_response_bytes=settings.model.max_response_kb * 1024,
        )
        historical = OptMemAdapter(
            executable=resolve_optmem_executable(),
            memory_directory=runtime_paths.memory,
            wake_budget_bytes=settings.memory.wake_budget_bytes,
        )
        compressor = OllamaMemoryCompressor(
            model=settings.model.model_name,
            endpoint=settings.model.endpoint,
            timeout_seconds=settings.model.timeout_seconds,
            keep_alive=settings.model.keep_alive,
            context_tokens=settings.model.context_tokens,
        )
        timeline = ActivityTimelineService(
            engine=engine,
            sessionizer=ModelActivitySessionizer(
                session_gap=timedelta(seconds=settings.events.session_gap_seconds),
                max_session_duration=timedelta(
                    seconds=settings.events.max_session_duration_seconds
                ),
            ),
            builder=SessionizedModelEventBuilder(clock=processor_clock),
            clock=processor_clock,
            identifiers=identifiers,
        )
        service = ContextRefreshService(
            model_processing=LocalModelProcessingService(
                engine=engine,
                raw_store=raw_store,
                provider=provider,
                endpoint=settings.model.endpoint,
                configured_model=settings.model.model_name,
                max_image_bytes=settings.model.max_image_mb * 1024 * 1024,
                clock=processor_clock,
                identifiers=identifiers,
            ),
            model_events=LocalModelEventService(
                engine=engine,
                builder=ModelTransformationEventBuilder(clock=processor_clock),
                clock=processor_clock,
                identifiers=identifiers,
            ),
            timeline=timeline,
            patterns=PatternAnalysisService(
                engine=engine,
                timeline_service=timeline,
                pattern_engine=TemporalPatternEngine(clock=processor_clock),
                clock=processor_clock,
                identifiers=identifiers,
            ),
            candidates=PatternCandidateService(
                engine=engine,
                producer=PatternCandidateProducer(clock=processor_clock),
                clock=processor_clock,
                identifiers=identifiers,
            ),
            evaluation=CandidateEvaluationService(
                engine=engine,
                worker=TransparentCandidateWorker(clock=processor_clock),
                clock=processor_clock,
                identifiers=identifiers,
            ),
            promotion=MemoryPromotionService(
                engine=engine,
                memory_store=historical,
                clock=processor_clock,
                identifiers=identifiers,
            ),
            maintenance=MemoryMaintenanceService(
                engine=engine,
                memory_store=historical,
                compressor=compressor,
                clock=processor_clock,
                identifiers=identifiers,
                max_compressions=settings.memory.max_compressions_per_cycle,
            ),
            projection=ActiveMemoryProjectionService(
                engine=engine,
                projection_root=runtime_paths.memory_projection,
                memory_store_factory=lambda directory: OptMemAdapter(
                    executable=resolve_optmem_executable(),
                    memory_directory=directory,
                    wake_budget_bytes=settings.memory.wake_budget_bytes,
                ),
                compressor=compressor,
            ),
        )
    except Exception:
        if engine is not None:
            engine.dispose()
        lease.release()
        raise
    return ConfiguredContextProcessor(
        service=service,
        engine=engine,
        clock=processor_clock,
        analysis_interval=timedelta(
            seconds=settings.processing.analysis_interval_seconds
        ),
        analysis_window=timedelta(days=settings.processing.analysis_window_days),
        comparison_period=timedelta(days=settings.processing.comparison_period_days),
        lease=lease,
    )
