"""Top-level CONTX command-line application."""

from enum import StrEnum
from typing import Annotated, Never

import typer
from sqlalchemy import Engine

from contx import __version__
from contx.application import PipelineService
from contx.candidates.rules import VerticalSliceCandidateProducer
from contx.collectors.macos import ActiveApplicationCollector
from contx.collectors.synthetic import SyntheticCollector
from contx.db import (
    create_database_engine,
    current_database_revision,
    head_database_revision,
    upgrade_database,
)
from contx.errors import ContxError
from contx.events.rules import VerticalSliceEventBuilder
from contx.memory_worker import ThresholdMemoryWorker
from contx.models import SystemClock, UuidIdentifierSource
from contx.settings import (
    initialize_runtime_paths,
    load_settings,
    resolve_runtime_paths,
)

app = typer.Typer(
    add_completion=False,
    help="Local-first personal context memory for AI agents.",
    no_args_is_help=True,
)


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
    try:
        paths = resolve_runtime_paths()
        initialize_runtime_paths(paths)
        load_settings(paths)
        revision = upgrade_database(paths.database_file)
    except ContxError as error:
        _abort(error)
    typer.echo(
        f"CONTX initialized at database revision {revision}; "
        "background collection is disabled."
    )


@app.command()
def status() -> None:
    """Show local initialization and safety state."""
    try:
        paths = resolve_runtime_paths()
        settings = load_settings(paths)
        current_revision = current_database_revision(paths.database_file)
        head_revision = head_database_revision()
    except ContxError as error:
        _abort(error)

    config_state = "present" if paths.config_file.is_file() else "missing"
    database_state = "present" if paths.database_file.is_file() else "missing"
    typer.echo(f"configuration: {config_state}")
    typer.echo(f"database: {database_state}")
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
    typer.echo(
        "window titles: "
        + ("enabled" if settings.collection.window_titles_enabled else "disabled")
    )


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
        load_settings(paths)
        upgrade_database(paths.database_file)
        engine = create_database_engine(paths.database_file)
        clock = SystemClock()
        identifiers = UuidIdentifierSource()
        collector = (
            SyntheticCollector.default(clock=clock, identifiers=identifiers)
            if source is RunSource.SYNTHETIC
            else ActiveApplicationCollector(clock=clock, identifiers=identifiers)
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


def _abort(error: ContxError) -> Never:
    typer.echo(f"Error: {error}", err=True)
    raise typer.Exit(code=2)


def main() -> None:
    """Run the CONTX CLI."""
    app()
