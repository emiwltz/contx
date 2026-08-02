"""Top-level CONTX command-line application."""

import re
from datetime import timedelta
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Never
from uuid import UUID

import typer
from sqlalchemy import Engine

from contx import __version__
from contx.application import (
    LocalModelEventService,
    LocalModelProcessingService,
    PipelineService,
    RawPurgeService,
)
from contx.candidates.rules import VerticalSliceCandidateProducer
from contx.collection import (
    CollectionControlService,
    CollectionPolicy,
    ControlledMetadataCollector,
)
from contx.collectors import Collector
from contx.collectors.macos import (
    ActiveApplicationCollector,
    detect_collection_capabilities,
)
from contx.collectors.synthetic import SyntheticCollector
from contx.daemon import probe_daemon_lease
from contx.db import (
    create_database_engine,
    current_database_revision,
    head_database_revision,
    session_scope,
    upgrade_database,
)
from contx.db.repositories import ModelEventRepository, ModelTransformationRepository
from contx.errors import ContxError, RawStoreError
from contx.events import MODEL_EVENT_PROCESSING_VERSION, ModelTransformationEventBuilder
from contx.events.rules import VerticalSliceEventBuilder
from contx.memory_store import (
    MemoryStore,
    OptMemAdapter,
    resolve_optmem_executable,
)
from contx.memory_worker import ThresholdMemoryWorker
from contx.model_provider import (
    OUTPUT_SCHEMA_VERSION,
    PROMPT_VERSION,
    OllamaModelProvider,
)
from contx.models import (
    CollectionControl,
    ExclusionRuleType,
    SystemClock,
    UuidIdentifierSource,
)
from contx.raw_store import FilesystemRawStore
from contx.settings import (
    ModelSettings,
    initialize_runtime_paths,
    load_settings,
    resolve_runtime_paths,
)

app = typer.Typer(
    add_completion=False,
    help="Local-first personal context memory for AI agents.",
    no_args_is_help=True,
)
exclusions_app = typer.Typer(help="Manage pre-capture exclusion rules.")
model_app = typer.Typer(help="Inspect the mandatory local multimodal model.")
app.add_typer(exclusions_app, name="exclusions")
app.add_typer(model_app, name="model")


class RunSource(StrEnum):
    """Explicit one-shot sources available before any daemon exists."""

    SYNTHETIC = "synthetic"
    ACTIVE_APP = "active-app"


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit()


@app.callback()
def root(
    version: Annotated[
        bool | None,
        typer.Option("--version", callback=_version_callback, is_eager=True),
    ] = None,
) -> None:
    """Manage CONTX local context memory."""


@app.command("init")
def initialize() -> None:
    """Create private local runtime paths without enabling collection."""
    engine: Engine | None = None
    try:
        paths = resolve_runtime_paths()
        initialize_runtime_paths(paths)
        load_settings(paths)
        revision = upgrade_database(paths.database_file)
        engine = create_database_engine(paths.database_file)
        CollectionControlService(engine=engine, clock=SystemClock()).initialize()
    except ContxError as error:
        _abort(error)
    finally:
        if engine is not None:
            engine.dispose()
    typer.echo(
        f"CONTX initialized at database revision {revision}; "
        "background collection is disabled."
    )


@app.command()
def status() -> None:
    """Show local initialization and safety state."""
    engine: Engine | None = None
    control: CollectionControl | None = None
    raw_usage: int | None = None
    model_backlog: int | None = None
    abandoned_transformations: int | None = None
    model_event_backlog: int | None = None
    try:
        paths = resolve_runtime_paths()
        settings = load_settings(paths)
        current_revision = current_database_revision(paths.database_file)
        head_revision = head_database_revision()
        if current_revision == head_revision:
            engine = create_database_engine(paths.database_file)
            controls = CollectionControlService(engine=engine, clock=SystemClock())
            controls.initialize()
            control = controls.control()
            with session_scope(engine) as session:
                model_repository = ModelTransformationRepository(session)
                model_backlog = model_repository.backlog_count(
                    provider="ollama",
                    endpoint=settings.model.endpoint,
                    configured_model=settings.model.model_name,
                    prompt_version=PROMPT_VERSION,
                    output_schema_version=OUTPUT_SCHEMA_VERSION,
                ) + model_repository.unqueued_screenshot_count(
                    at=SystemClock().now(),
                    provider="ollama",
                    endpoint=settings.model.endpoint,
                    configured_model=settings.model.model_name,
                    prompt_version=PROMPT_VERSION,
                    output_schema_version=OUTPUT_SCHEMA_VERSION,
                )
                abandoned_transformations = model_repository.abandoned_count()
                model_event_backlog = ModelEventRepository(session).pending_count(
                    processing_version=MODEL_EVENT_PROCESSING_VERSION
                )
        if paths.raw.is_dir():
            raw_usage = _build_raw_store(
                paths.raw, budget_mb=settings.collection.raw_disk_budget_mb
            ).usage_bytes()
    except ContxError as error:
        _abort(error)
    finally:
        if engine is not None:
            engine.dispose()

    config_state = "present" if paths.config_file.is_file() else "missing"
    database_state = "present" if paths.database_file.is_file() else "missing"
    memory_state = (
        "initialized"
        if (paths.memory / "LOG.txt").is_file() and (paths.memory / "config").is_file()
        else "not initialized"
    )
    typer.echo(f"configuration: {config_state}")
    typer.echo(f"database: {database_state}")
    typer.echo(f"memory: {memory_state}")
    schema_state = "current" if current_revision == head_revision else "not current"
    typer.echo(f"schema: {schema_state}")
    typer.echo(
        "background collection: "
        + (
            "enabled"
            if settings.collection.background_collection_enabled
            else "disabled"
        )
    )
    daemon_state = (
        probe_daemon_lease(paths.daemon_lock) if paths.processing.is_dir() else None
    )
    typer.echo(
        "collector daemon: "
        + (
            "running"
            if daemon_state is not None and daemon_state.running
            else "stopped"
        )
    )
    if (
        daemon_state is not None
        and daemon_state.running
        and daemon_state.pid is not None
    ):
        typer.echo(f"collector daemon pid: {daemon_state.pid}")
    typer.echo(
        "window titles: "
        + ("enabled" if settings.collection.window_titles_enabled else "disabled")
    )
    typer.echo(f"raw retention: {settings.collection.raw_retention_hours}h")
    if raw_usage is not None:
        typer.echo(f"raw usage: {raw_usage} bytes")
    if model_backlog is not None:
        typer.echo(f"model backlog: {model_backlog}")
    if abandoned_transformations is not None:
        typer.echo(f"model abandoned: {abandoned_transformations}")
    if model_event_backlog is not None:
        typer.echo(f"model event backlog: {model_event_backlog}")
    collection_state = (
        "unavailable"
        if control is None
        else ("paused" if control.is_paused(at=SystemClock().now()) else "active")
    )
    typer.echo(f"collection: {collection_state}")
    if control is not None and control.pause_until is not None:
        typer.echo(f"pause until: {control.pause_until.isoformat()}")


@app.command()
def capabilities() -> None:
    """Show collection APIs and permissions without requesting access."""
    try:
        paths = resolve_runtime_paths()
        settings = load_settings(paths)
        detected = detect_collection_capabilities(settings.collection)
    except ContxError as error:
        _abort(error)
    for capability in detected:
        detail = (
            "" if capability.reason_code is None else f" ({capability.reason_code})"
        )
        typer.echo(f"{capability.name}: {capability.status.value}{detail}")
        if capability.settings_path is not None:
            typer.echo(f"  settings: {capability.settings_path}")


@model_app.command("status")
def local_model_status() -> None:
    """Preflight the configured loopback runtime without sending user content."""
    try:
        paths = resolve_runtime_paths()
        settings = load_settings(paths)
        status = _build_local_model_provider(settings.model).status()
    except ContxError as error:
        _abort(error)
    typer.echo(f"provider: {status.provider}")
    typer.echo(f"endpoint: {status.endpoint}")
    runtime_state = "available" if status.runtime_available else "unavailable"
    typer.echo(f"runtime: {runtime_state}")
    if status.runtime_version is not None:
        typer.echo(f"runtime version: {status.runtime_version}")
    model_state = "installed" if status.model_available else "missing"
    typer.echo(f"model: {model_state} ({status.model})")
    if status.model_digest is not None:
        typer.echo(f"model digest: {status.model_digest}")
    if status.reason_code is not None:
        typer.echo(f"reason: {status.reason_code}")


@app.command()
def process() -> None:
    """Process a bounded screenshot backlog through the configured local model."""
    engine: Engine | None = None
    try:
        paths = resolve_runtime_paths()
        initialize_runtime_paths(paths)
        settings = load_settings(paths)
        upgrade_database(paths.database_file)
        engine = create_database_engine(paths.database_file)
        clock = SystemClock()
        identifiers = UuidIdentifierSource()
        model_result = LocalModelProcessingService(
            engine=engine,
            raw_store=_build_raw_store(
                paths.raw,
                budget_mb=settings.collection.raw_disk_budget_mb,
            ),
            provider=_build_local_model_provider(settings.model),
            endpoint=settings.model.endpoint,
            configured_model=settings.model.model_name,
            max_image_bytes=settings.model.max_image_mb * 1024 * 1024,
            clock=clock,
            identifiers=identifiers,
        ).run_once()
        event_result = LocalModelEventService(
            engine=engine,
            builder=ModelTransformationEventBuilder(clock=clock),
            clock=clock,
            identifiers=identifiers,
        ).run_once()
    except ContxError as error:
        _abort(error)
    finally:
        if engine is not None:
            engine.dispose()

    runtime_state = (
        "available" if model_result.runtime.runtime_available else "unavailable"
    )
    model_state = "installed" if model_result.runtime.model_available else "missing"
    typer.echo(f"run: {model_result.run.status.value}")
    typer.echo(f"runtime: {runtime_state}")
    typer.echo(f"model: {model_state} ({model_result.runtime.model})")
    typer.echo(f"queued transformations: {len(model_result.queued_transformation_ids)}")
    typer.echo(
        f"succeeded transformations: {len(model_result.succeeded_transformation_ids)}"
    )
    typer.echo(f"failed transformations: {len(model_result.failed_transformation_ids)}")
    typer.echo(
        f"abandoned transformations: {len(model_result.abandoned_transformation_ids)}"
    )
    typer.echo(
        f"recovered transformations: {len(model_result.recovered_transformation_ids)}"
    )
    typer.echo(f"model backlog: {model_result.backlog_count}")
    typer.echo(f"model abandoned total: {model_result.total_abandoned_count}")
    typer.echo(f"events built: {len(event_result.events)}")
    typer.echo(f"model event backlog: {event_result.backlog_count}")
    if not model_result.succeeded or not event_result.succeeded:
        raise typer.Exit(code=2)


@app.command("run-once")
def run_once(
    source: Annotated[
        RunSource,
        typer.Option("--source", help="Bounded source to collect explicitly."),
    ] = RunSource.SYNTHETIC,
) -> None:
    """Run one foreground collection and processing cycle."""
    engine: Engine | None = None
    try:
        paths = resolve_runtime_paths()
        initialize_runtime_paths(paths)
        settings = load_settings(paths)
        upgrade_database(paths.database_file)
        engine = create_database_engine(paths.database_file)
        memory_store = _build_memory_store(paths.memory)
        clock = SystemClock()
        identifiers = UuidIdentifierSource()
        controls = CollectionControlService(engine=engine, clock=clock)
        controls.initialize()
        retention = timedelta(hours=settings.collection.raw_retention_hours)
        if source is RunSource.ACTIVE_APP:
            purge_result = RawPurgeService(
                engine=engine,
                raw_store=_build_raw_store(
                    paths.raw,
                    budget_mb=settings.collection.raw_disk_budget_mb,
                ),
                clock=clock,
                identifiers=identifiers,
            ).run()
            if not purge_result.succeeded:
                raise RawStoreError(
                    "Expired raw data could not be purged; collection did not start"
                )
        collector: Collector = (
            SyntheticCollector.default(
                clock=clock,
                identifiers=identifiers,
                retention=retention,
            )
            if source is RunSource.SYNTHETIC
            else ActiveApplicationCollector(
                clock=clock,
                identifiers=identifiers,
                retention=retention,
            )
        )
        if source is RunSource.ACTIVE_APP:
            collector = ControlledMetadataCollector(
                collector,
                controls=controls,
                policy=CollectionPolicy(),
                clock=clock,
                identifiers=identifiers,
                retention=retention,
                retain_excluded_activity=(settings.collection.retain_excluded_activity),
            )
        result = PipelineService(
            engine=engine,
            event_builder=VerticalSliceEventBuilder(
                clock=clock, identifiers=identifiers
            ),
            candidate_producer=VerticalSliceCandidateProducer(
                clock=clock, identifiers=identifiers
            ),
            memory_worker=ThresholdMemoryWorker(),
            memory_store=memory_store,
            clock=clock,
            identifiers=identifiers,
        ).run_once(collector)
    except ContxError as error:
        _abort(error)
    finally:
        if engine is not None:
            engine.dispose()

    typer.echo(f"run: {result.run.status.value}")
    typer.echo(f"observations: {len(result.observations)}")
    typer.echo(f"events: {len(result.events)}")
    typer.echo(f"accepted candidates: {len(result.accepted_candidates)}")
    typer.echo(f"rejected candidates: {len(result.rejected_candidates)}")
    typer.echo(f"stored memories: {len(result.memory_links)}")
    if result.memory_maintenance_required:
        typer.echo("memory maintenance: required")


@app.command()
def pause(
    duration: Annotated[
        str | None,
        typer.Option(
            "--for",
            help="Optional duration such as 15m, 2h, or 1d; omit for indefinite.",
        ),
    ] = None,
) -> None:
    """Pause real collection immediately."""
    engine: Engine | None = None
    try:
        engine, controls = _open_collection_controls()
        parsed = None if duration is None else _parse_duration(duration)
        control = controls.pause(duration=parsed)
    except ContxError as error:
        _abort(error)
    finally:
        if engine is not None:
            engine.dispose()
    if control.pause_until is None:
        typer.echo("collection: paused indefinitely")
    else:
        typer.echo(f"collection: paused until {control.pause_until.isoformat()}")


@app.command()
def resume() -> None:
    """Resume collection after an explicit pause."""
    engine: Engine | None = None
    try:
        engine, controls = _open_collection_controls()
        controls.resume()
    except ContxError as error:
        _abort(error)
    finally:
        if engine is not None:
            engine.dispose()
    typer.echo("collection: active")


@app.command()
def purge() -> None:
    """Delete expired raw files and tombstone their source records."""
    engine: Engine | None = None
    try:
        paths = resolve_runtime_paths()
        initialize_runtime_paths(paths)
        settings = load_settings(paths)
        upgrade_database(paths.database_file)
        engine = create_database_engine(paths.database_file)
        result = RawPurgeService(
            engine=engine,
            raw_store=_build_raw_store(
                paths.raw,
                budget_mb=settings.collection.raw_disk_budget_mb,
            ),
            clock=SystemClock(),
            identifiers=UuidIdentifierSource(),
        ).run()
        if not result.succeeded:
            raise RawStoreError(
                "Some expired raw records could not be purged; retry is safe"
            )
    except ContxError as error:
        _abort(error)
    finally:
        if engine is not None:
            engine.dispose()
    typer.echo(f"purged observations: {len(result.purged_observation_ids)}")
    typer.echo(f"reclaimed bytes: {result.bytes_reclaimed}")
    typer.echo(f"orphan artifacts deleted: {result.orphan_artifacts_deleted}")


@exclusions_app.command("list")
def list_exclusions() -> None:
    """List built-in and user exclusion rules."""
    engine: Engine | None = None
    try:
        engine, controls = _open_collection_controls()
        rules = controls.rules()
    except ContxError as error:
        _abort(error)
    finally:
        if engine is not None:
            engine.dispose()
    for rule in rules:
        state = "enabled" if rule.enabled else "disabled"
        origin = "built-in" if rule.built_in else "user"
        typer.echo(f"{rule.id} {rule.rule_type.value} {state} {origin} {rule.pattern}")


@exclusions_app.command("add")
def add_exclusion(
    pattern: Annotated[str, typer.Argument(help="Exact value or contained text.")],
    rule_type: Annotated[
        ExclusionRuleType,
        typer.Option("--type", help="Metadata field matched before capture."),
    ] = ExclusionRuleType.APP_BUNDLE_ID,
) -> None:
    """Add one user-controlled exclusion rule."""
    engine: Engine | None = None
    try:
        engine, controls = _open_collection_controls()
        rule = controls.add_rule(
            rule_id=UuidIdentifierSource().new(),
            rule_type=rule_type,
            pattern=pattern,
        )
    except ContxError as error:
        _abort(error)
    finally:
        if engine is not None:
            engine.dispose()
    typer.echo(f"exclusion added: {rule.id}")


@exclusions_app.command("enable")
def enable_exclusion(rule_id: UUID) -> None:
    """Enable an exclusion rule."""
    _set_exclusion_enabled(rule_id, enabled=True)


@exclusions_app.command("disable")
def disable_exclusion(rule_id: UUID) -> None:
    """Disable an exclusion rule without deleting it."""
    _set_exclusion_enabled(rule_id, enabled=False)


@exclusions_app.command("delete")
def delete_exclusion(rule_id: UUID) -> None:
    """Delete a user rule; built-in rules can only be disabled."""
    engine: Engine | None = None
    try:
        engine, controls = _open_collection_controls()
        controls.delete_rule(rule_id)
    except ContxError as error:
        _abort(error)
    finally:
        if engine is not None:
            engine.dispose()
    typer.echo(f"exclusion deleted: {rule_id}")


@app.command()
def wake(
    part: Annotated[int, typer.Option(min=1, help="Context page to read.")] = 1,
    snapshot: Annotated[
        int | None,
        typer.Option(min=0, help="Stable OptMem snapshot for later pages."),
    ] = None,
) -> None:
    """Print semantic memory context directly from MemoryStore."""
    try:
        paths = resolve_runtime_paths()
        memory = _build_memory_store(paths.memory)
        result = memory.wake(part=part, snapshot=snapshot)
    except ContxError as error:
        _abort(error)
    typer.echo(result.content, nl=False)


def _build_memory_store(memory_directory: Path) -> MemoryStore:
    return OptMemAdapter(
        executable=resolve_optmem_executable(),
        memory_directory=memory_directory,
    )


def _build_local_model_provider(settings: ModelSettings) -> OllamaModelProvider:
    return OllamaModelProvider(
        model=settings.model_name,
        endpoint=settings.endpoint,
        timeout_seconds=settings.timeout_seconds,
        keep_alive=settings.keep_alive,
        context_tokens=settings.context_tokens,
        max_output_tokens=settings.max_output_tokens,
        max_image_bytes=settings.max_image_mb * 1024 * 1024,
        max_response_bytes=settings.max_response_kb * 1024,
    )


def _build_raw_store(raw_directory: Path, *, budget_mb: int) -> FilesystemRawStore:
    return FilesystemRawStore(
        raw_directory,
        disk_budget_bytes=budget_mb * 1024 * 1024,
    )


def _open_collection_controls() -> tuple[Engine, CollectionControlService]:
    paths = resolve_runtime_paths()
    initialize_runtime_paths(paths)
    load_settings(paths)
    upgrade_database(paths.database_file)
    engine = create_database_engine(paths.database_file)
    controls = CollectionControlService(engine=engine, clock=SystemClock())
    try:
        controls.initialize()
    except Exception:
        engine.dispose()
        raise
    return engine, controls


def _set_exclusion_enabled(rule_id: UUID, *, enabled: bool) -> None:
    engine: Engine | None = None
    try:
        engine, controls = _open_collection_controls()
        controls.set_rule_enabled(rule_id, enabled=enabled)
    except ContxError as error:
        _abort(error)
    finally:
        if engine is not None:
            engine.dispose()
    state = "enabled" if enabled else "disabled"
    typer.echo(f"exclusion {state}: {rule_id}")


def _parse_duration(value: str) -> timedelta:
    match = re.fullmatch(r"([1-9]\d*)([mhd])", value.strip().lower())
    if match is None:
        raise typer.BadParameter("duration must use <number>m, <number>h, or <number>d")
    amount = int(match.group(1))
    unit = match.group(2)
    duration = {
        "m": timedelta(minutes=amount),
        "h": timedelta(hours=amount),
        "d": timedelta(days=amount),
    }[unit]
    if duration > timedelta(days=30):
        raise typer.BadParameter("pause duration must not exceed 30 days")
    return duration


def _abort(error: ContxError) -> Never:
    typer.echo(f"Error: {error}", err=True)
    raise typer.Exit(code=2)


def main() -> None:
    """Run the CONTX CLI."""
    app()
