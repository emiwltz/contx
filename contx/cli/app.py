"""Top-level CONTX command-line application."""

from typing import Annotated

import typer

from contx import __version__

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


def main() -> None:
    """Run the CONTX CLI."""
    app()
