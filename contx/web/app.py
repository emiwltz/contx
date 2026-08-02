"""FastAPI application for the versioned loopback-only CONTX interface."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import Engine
from starlette.middleware.trustedhost import TrustedHostMiddleware

from contx import __version__
from contx.application import (
    ActiveMemoryProjectionService,
    AgentProposalAdoptionService,
    DataDeletionService,
    EventCorrectionService,
    InspectionService,
    MemoryCorrectionService,
    RawPurgeService,
)
from contx.collection import CollectionControlService
from contx.db import create_database_engine, upgrade_database
from contx.errors import ContxError
from contx.models import Clock, CollectionControl, SystemClock, UuidIdentifierSource
from contx.runtime import (
    build_active_memory_projection_service,
    build_agent_proposal_evaluator,
    build_local_model_provider,
    build_memory_correction_composer,
    build_memory_store,
    build_raw_store,
)
from contx.settings import (
    AppSettings,
    RuntimePaths,
    initialize_runtime_paths,
    load_settings,
    resolve_runtime_paths,
)
from contx.web.schemas import (
    ActivityResponse,
    AgentResponse,
    CollectionResponse,
    EmptyRequest,
    EventCorrectionRequest,
    EventCorrectionResponse,
    ExclusionCreateRequest,
    ExclusionUpdateRequest,
    ExclusionView,
    FullDataDeletionRequest,
    FullDataDeletionResponse,
    ImmediateRawPurgeRequest,
    MemoryCorrectionRequest,
    MemoryCorrectionResponse,
    MemoryResponse,
    PatternsResponse,
    PatternView,
    PauseRequest,
    PrivacyResponse,
    ProcessingResponse,
    ProcessingRunView,
    ProposalDecisionResponse,
    ProposalRejectionRequest,
    RawPurgeResponse,
    SettingsResponse,
    StatusResponse,
    WakeResponse,
)

API_PREFIX = "/api/v1"
DEFAULT_WEB_PORT = 8711
MAX_PAGE_LIMIT = 500
_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
type PageLimit = Annotated[int, Query(ge=1, le=MAX_PAGE_LIMIT)]


@dataclass(slots=True)
class WebRuntime:
    """All configured services shared by one local web process."""

    engine: Engine
    paths: RuntimePaths
    settings: AppSettings
    clock: Clock
    controls: CollectionControlService
    inspection: InspectionService
    active_memory: ActiveMemoryProjectionService
    raw_purge: RawPurgeService | None = None
    event_corrections: EventCorrectionService | None = None
    memory_corrections: MemoryCorrectionService | None = None
    proposal_adoption: AgentProposalAdoptionService | None = None
    data_deletion: DataDeletionService | None = None
    port: int = DEFAULT_WEB_PORT
    owns_engine: bool = False


def create_runtime(*, port: int = DEFAULT_WEB_PORT) -> WebRuntime:
    """Open the real local runtime without enabling collection or permissions."""
    _validate_port(port)
    paths = resolve_runtime_paths()
    initialize_runtime_paths(paths)
    settings = load_settings(paths)
    upgrade_database(paths.database_file)
    engine = create_database_engine(paths.database_file)
    clock = SystemClock()
    controls = CollectionControlService(engine=engine, clock=clock)
    try:
        controls.initialize()
        raw_store = build_raw_store(paths, settings)
        inspection = InspectionService(
            engine=engine,
            paths=paths,
            settings=settings,
            raw_store=raw_store,
            model_provider=build_local_model_provider(
                settings.model,
                timeout_seconds=min(2.0, settings.model.timeout_seconds),
            ),
            clock=clock,
        )
        active_memory = build_active_memory_projection_service(
            engine=engine,
            paths=paths,
            settings=settings,
        )
        historical_memory = build_memory_store(
            paths.memory,
            wake_budget_bytes=settings.memory.wake_budget_bytes,
        )
        raw_purge = RawPurgeService(
            engine=engine,
            raw_store=raw_store,
            clock=clock,
            identifiers=UuidIdentifierSource(),
        )
        event_corrections = EventCorrectionService(engine=engine)
        memory_corrections = MemoryCorrectionService(
            engine=engine,
            memory_store=historical_memory,
            composer=build_memory_correction_composer(settings.model),
            clock=clock,
        )
        proposal_adoption = AgentProposalAdoptionService(
            engine=engine,
            memory_store=historical_memory,
            evaluator=build_agent_proposal_evaluator(settings.model),
            clock=clock,
        )
        data_deletion = DataDeletionService(paths=paths, engine=engine)
    except Exception:
        engine.dispose()
        raise
    return WebRuntime(
        engine=engine,
        paths=paths,
        settings=settings,
        clock=clock,
        controls=controls,
        inspection=inspection,
        active_memory=active_memory,
        raw_purge=raw_purge,
        event_corrections=event_corrections,
        memory_corrections=memory_corrections,
        proposal_adoption=proposal_adoption,
        data_deletion=data_deletion,
        port=port,
        owns_engine=True,
    )


def create_app(runtime: WebRuntime | None = None) -> FastAPI:
    """Build one API application, optionally around deterministic test services."""
    selected_runtime = create_runtime() if runtime is None else runtime

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            if selected_runtime.owns_engine:
                selected_runtime.engine.dispose()

    app = FastAPI(
        title="CONTX local API",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=f"{API_PREFIX}/openapi.json",
        lifespan=lifespan,
    )
    app.state.runtime = selected_runtime
    app.state.data_deleted = False
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["127.0.0.1", "localhost", "[::1]", "testserver"],
        www_redirect=False,
    )

    @app.middleware("http")
    async def local_request_boundary(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if app.state.data_deleted and request.url.path.startswith(API_PREFIX):
            return JSONResponse(
                status_code=410,
                content={"detail": "CONTX local data has been deleted."},
            )
        origin = request.headers.get("origin")
        if origin is not None and not _same_origin(origin, request):
            return JSONResponse(
                status_code=403,
                content={"detail": "Cross-origin requests are not allowed."},
            )
        fetch_site = request.headers.get("sec-fetch-site")
        if fetch_site not in {None, "same-origin", "none"}:
            return JSONResponse(
                status_code=403,
                content={"detail": "Cross-site requests are not allowed."},
            )
        if request.method in _UNSAFE_METHODS:
            content_type = request.headers.get("content-type", "").split(";", 1)[0]
            if content_type.lower() != "application/json":
                return JSONResponse(
                    status_code=415,
                    content={"detail": "Mutations require application/json."},
                )
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; base-uri 'none'; frame-ancestors 'none'; "
            "form-action 'self'; object-src 'none'"
        )
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    @app.exception_handler(ContxError)
    async def contx_error_handler(_request: Request, error: ContxError) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={"detail": str(error), "error_type": type(error).__name__},
        )

    @app.get(f"{API_PREFIX}/status", response_model=StatusResponse)
    def status() -> StatusResponse:
        inspection = selected_runtime.inspection.overview()
        return StatusResponse.from_inspection(
            inspection,
            product_version=__version__,
            background_enabled=(
                selected_runtime.settings.collection.background_collection_enabled
            ),
            now=selected_runtime.clock.now(),
        )

    @app.post(f"{API_PREFIX}/pause", response_model=CollectionResponse)
    def pause(payload: PauseRequest) -> CollectionResponse:
        duration = (
            None
            if payload.duration_minutes is None
            else timedelta(minutes=payload.duration_minutes)
        )
        return _collection_response(
            selected_runtime.controls.pause(duration=duration),
            clock=selected_runtime.clock,
        )

    @app.post(f"{API_PREFIX}/resume", response_model=CollectionResponse)
    def resume(_payload: EmptyRequest) -> CollectionResponse:
        return _collection_response(
            selected_runtime.controls.resume(),
            clock=selected_runtime.clock,
        )

    @app.get(f"{API_PREFIX}/exclusions", response_model=tuple[ExclusionView, ...])
    def exclusions() -> tuple[ExclusionView, ...]:
        return tuple(
            ExclusionView.from_record(item)
            for item in selected_runtime.controls.rules()
        )

    @app.post(
        f"{API_PREFIX}/exclusions",
        response_model=ExclusionView,
        status_code=201,
    )
    def create_exclusion(payload: ExclusionCreateRequest) -> ExclusionView:
        rule = selected_runtime.controls.add_rule(
            rule_id=uuid4(),
            rule_type=payload.rule_type,
            pattern=payload.pattern,
        )
        return ExclusionView.from_record(rule)

    @app.patch(
        f"{API_PREFIX}/exclusions/{{rule_id}}",
        response_model=ExclusionView,
    )
    def update_exclusion(
        rule_id: UUID, payload: ExclusionUpdateRequest
    ) -> ExclusionView:
        rule = selected_runtime.controls.set_rule_enabled(
            rule_id, enabled=payload.enabled
        )
        return ExclusionView.from_record(rule)

    @app.delete(
        f"{API_PREFIX}/exclusions/{{rule_id}}",
        status_code=204,
        response_class=Response,
    )
    def delete_exclusion(rule_id: UUID, _payload: EmptyRequest) -> Response:
        selected_runtime.controls.delete_rule(rule_id)
        return Response(status_code=204)

    @app.get(f"{API_PREFIX}/activity", response_model=ActivityResponse)
    def activity(limit: PageLimit = 100) -> ActivityResponse:
        return ActivityResponse.from_inspection(
            selected_runtime.inspection.activity(limit=limit)
        )

    @app.post(
        f"{API_PREFIX}/events/{{event_id}}/corrections",
        response_model=EventCorrectionResponse,
        status_code=201,
    )
    def correct_event(
        event_id: UUID,
        payload: EventCorrectionRequest,
    ) -> EventCorrectionResponse:
        service = selected_runtime.event_corrections
        if service is None:
            raise HTTPException(
                status_code=503,
                detail="Event correction service is unavailable.",
            )
        correction = service.correct_summary(
            event_id=event_id,
            summary=payload.summary,
            reason=payload.reason,
            correction_id=uuid4(),
            created_at=selected_runtime.clock.now(),
        )
        return EventCorrectionResponse(
            correction_id=correction.id,
            event_id=correction.target_event_id,
            supersedes_correction_id=correction.supersedes_correction_id,
            created_at=correction.created_at,
        )

    @app.get(f"{API_PREFIX}/patterns", response_model=PatternsResponse)
    def patterns(limit: PageLimit = 100) -> PatternsResponse:
        now = selected_runtime.clock.now()
        return PatternsResponse(
            patterns=tuple(
                PatternView.from_record(item, now=now)
                for item in selected_runtime.inspection.patterns(limit=limit)
            )
        )

    @app.get(f"{API_PREFIX}/memory", response_model=MemoryResponse)
    def memory(limit: PageLimit = 100) -> MemoryResponse:
        return MemoryResponse.from_inspection(
            selected_runtime.inspection.memory(limit=limit)
        )

    @app.post(
        f"{API_PREFIX}/memory/{{memory_id}}/corrections",
        response_model=MemoryCorrectionResponse,
        status_code=201,
    )
    def correct_memory(
        memory_id: UUID,
        payload: MemoryCorrectionRequest,
    ) -> MemoryCorrectionResponse:
        service = selected_runtime.memory_corrections
        if service is None:
            raise HTTPException(
                status_code=503,
                detail="Memory correction service is unavailable.",
            )
        correction = service.correct(
            memory_id=memory_id,
            replacement=payload.replacement,
        )
        return MemoryCorrectionResponse(
            memory_id=correction.memory_link.id,
            candidate_id=correction.candidate.id,
            superseded_memory_id=correction.superseded_memory_id,
            replayed=correction.replayed,
            maintenance_required=correction.maintenance_required,
        )

    @app.get(f"{API_PREFIX}/memory/wake", response_model=WakeResponse)
    def wake(
        part: Annotated[int, Query(ge=1)] = 1,
        snapshot: Annotated[int | None, Query(ge=0)] = None,
    ) -> WakeResponse:
        result = selected_runtime.active_memory.wake(part=part, snapshot=snapshot)
        return WakeResponse(
            content=result.wake.content,
            complete=result.wake.complete,
            maintenance_required=result.wake.maintenance_required,
            snapshot=result.wake.snapshot,
            next_part=result.wake.next_part,
            active_memory_count=result.projection.active_memory_count,
            projection_generation=result.projection.generation,
        )

    @app.get(f"{API_PREFIX}/privacy", response_model=PrivacyResponse)
    def privacy(limit: PageLimit = 100) -> PrivacyResponse:
        return PrivacyResponse.from_inspection(
            selected_runtime.inspection.privacy(limit=limit)
        )

    @app.delete(
        f"{API_PREFIX}/privacy/raw-artifacts",
        response_model=RawPurgeResponse,
    )
    def purge_raw_artifacts(
        _payload: ImmediateRawPurgeRequest,
    ) -> RawPurgeResponse:
        service = selected_runtime.raw_purge
        if service is None:
            raise HTTPException(
                status_code=503,
                detail="Raw purge service is unavailable.",
            )
        result = service.run(include_unexpired=True)
        return RawPurgeResponse(
            processing_run_id=result.run.id,
            status=result.run.status.value,
            purged_observation_ids=result.purged_observation_ids,
            failed_observation_ids=result.failed_observation_ids,
            bytes_reclaimed=result.bytes_reclaimed,
            orphan_artifacts_deleted=result.orphan_artifacts_deleted,
        )

    @app.get(f"{API_PREFIX}/processing", response_model=ProcessingResponse)
    def processing(limit: PageLimit = 100) -> ProcessingResponse:
        return ProcessingResponse(
            runs=tuple(
                ProcessingRunView.from_record(item)
                for item in selected_runtime.inspection.processing_runs(limit=limit)
            )
        )

    @app.get(f"{API_PREFIX}/agent", response_model=AgentResponse)
    def agent(limit: PageLimit = 100) -> AgentResponse:
        return AgentResponse.from_inspection(
            selected_runtime.inspection.agent(limit=limit)
        )

    @app.post(
        f"{API_PREFIX}/agent/proposals/{{proposal_id}}/adopt",
        response_model=ProposalDecisionResponse,
    )
    def adopt_proposal(
        proposal_id: UUID,
        _payload: EmptyRequest,
    ) -> ProposalDecisionResponse:
        service = selected_runtime.proposal_adoption
        if service is None:
            raise HTTPException(
                status_code=503,
                detail="Proposal adoption service is unavailable.",
            )
        result = service.adopt(proposal_id=proposal_id)
        return ProposalDecisionResponse(
            proposal_id=result.proposal.id,
            status=result.proposal.status.value,
            decision=result.build.decision.value,
            reason=result.build.reason_code.value,
            memory_id=(
                None if result.memory_link is None else result.memory_link.id
            ),
            replayed=result.replayed,
        )

    @app.post(
        f"{API_PREFIX}/agent/proposals/{{proposal_id}}/reject",
        response_model=ProposalDecisionResponse,
    )
    def reject_proposal(
        proposal_id: UUID,
        payload: ProposalRejectionRequest,
    ) -> ProposalDecisionResponse:
        service = selected_runtime.proposal_adoption
        if service is None:
            raise HTTPException(
                status_code=503,
                detail="Proposal adoption service is unavailable.",
            )
        proposal = service.reject(proposal_id=proposal_id, reason=payload.reason)
        return ProposalDecisionResponse(
            proposal_id=proposal.id,
            status=proposal.status.value,
            reason=proposal.reason,
        )

    @app.get(f"{API_PREFIX}/settings", response_model=SettingsResponse)
    def settings() -> SettingsResponse:
        return SettingsResponse.from_settings(
            selected_runtime.settings,
            port=selected_runtime.port,
        )

    @app.delete(
        f"{API_PREFIX}/system/data",
        response_model=FullDataDeletionResponse,
    )
    def delete_all_data(
        _payload: FullDataDeletionRequest,
    ) -> FullDataDeletionResponse:
        service = selected_runtime.data_deletion
        if service is None:
            raise HTTPException(
                status_code=503,
                detail="Full data deletion service is unavailable.",
            )
        result = service.delete_all()
        app.state.data_deleted = True
        return FullDataDeletionResponse(removed_stores=result.removed_stores)

    _mount_frontend(app)
    return app


def _collection_response(
    control: CollectionControl,
    *,
    clock: Clock,
) -> CollectionResponse:
    return CollectionResponse(
        paused=control.is_paused(at=clock.now()),
        paused_at=control.paused_at,
        pause_until=control.pause_until,
        updated_at=control.updated_at,
    )


def _same_origin(origin: str, request: Request) -> bool:
    return origin == f"{request.url.scheme}://{request.headers.get('host', '')}"


def _mount_frontend(app: FastAPI) -> None:
    static_root = Path(__file__).with_name("static")
    assets = static_root / "assets"
    app.mount("/assets", StaticFiles(directory=assets, check_dir=False), name="assets")

    @app.get("/", include_in_schema=False)
    def frontend_index() -> Response:
        return _frontend_response(static_root)

    @app.get("/{frontend_path:path}", include_in_schema=False)
    def frontend_fallback(frontend_path: str) -> Response:
        if frontend_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="API route not found")
        return _frontend_response(static_root)


def _frontend_response(static_root: Path) -> Response:
    index = static_root / "index.html"
    if not index.is_file():
        return JSONResponse(
            status_code=503,
            content={"detail": "The CONTX web interface has not been built."},
        )
    return FileResponse(index)


def _validate_port(port: int) -> None:
    if not 1024 <= port <= 65_535:
        raise ValueError("web port must be between 1024 and 65535")
