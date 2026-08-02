"""Top-level CONTX command-line application."""

from typing import Annotated

import typer

from contx import __version__
from contx.db import (
    current_database_revision,
    head_database_revision,
    upgrade_database,
)
from contx.errors import ContxError
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


def _abort(error: ContxError) -> None:
    typer.echo(f"Error: {error}", err=True)
    raise typer.Exit(code=2)


def main() -> None:
    """Run the CONTX CLI."""
    app()
