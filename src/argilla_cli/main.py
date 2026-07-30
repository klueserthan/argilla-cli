"""CLI entry point and global option surface.

All global flags are declared once, here, on the root callback. Commands read
them from :data:`argilla_cli.context.ctx` and
:mod:`argilla_cli.io_utils` rather than redeclaring their own copies.
"""

from __future__ import annotations

from typing import Annotated

import typer

from argilla_cli import __version__, io_utils
from argilla_cli.commands import config as config_cmd
from argilla_cli.commands import dataset as dataset_cmd
from argilla_cli.commands import server as server_cmd
from argilla_cli.commands import user as user_cmd
from argilla_cli.commands import workspace as workspace_cmd
from argilla_cli.context import ctx
from argilla_cli.io_utils import OutputFormat

app = typer.Typer(
    add_completion=True,
    no_args_is_help=True,
    help="argilla-cli: manage Argilla workspaces, datasets and users",
)

app.add_typer(config_cmd.app, name="config")
app.add_typer(workspace_cmd.app, name="workspace")
app.add_typer(dataset_cmd.app, name="dataset")
app.add_typer(user_cmd.app, name="user")
app.add_typer(server_cmd.app, name="server")


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"argilla-cli {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    output: Annotated[
        OutputFormat,
        typer.Option(
            "--output",
            "-o",
            help="Output format for structured results.",
            case_sensitive=False,
        ),
    ] = OutputFormat.TABLE,
    workspace: Annotated[
        str | None,
        typer.Option(
            "--workspace", "-w", help="Default workspace for this invocation."
        ),
    ] = None,
    profile: Annotated[
        str | None,
        typer.Option("--profile", "-p", help="Configuration profile to use."),
    ] = None,
    api_url: Annotated[
        str | None,
        typer.Option("--api-url", help="Override the Argilla API URL."),
    ] = None,
    api_key: Annotated[
        str | None,
        typer.Option("--api-key", help="Override the Argilla API key."),
    ] = None,
    yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Assume yes; never prompt for confirmation."),
    ] = False,
    verbose: Annotated[
        bool,
        typer.Option("--verbose", "-v", help="Include underlying error detail."),
    ] = False,
    quiet: Annotated[
        bool,
        typer.Option("--quiet", "-q", help="Suppress non-essential output."),
    ] = False,
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            "-V",
            callback=_version_callback,
            is_eager=True,
            help="Show the version and exit.",
        ),
    ] = False,
) -> None:
    """Global options. These apply to every subcommand."""
    io_utils.configure(output=output, verbose=verbose, quiet=quiet)
    ctx.reset()
    ctx.profile = profile
    ctx.api_url = api_url
    ctx.api_key = api_key
    ctx.assume_yes = yes
    ctx.default_workspace = workspace


def run() -> None:
    """Console-script entry point.

    Typer/Click already convert :class:`typer.Exit` into a process exit code
    and render usage errors, so this only exists to keep the entry point
    stable. Catching broad exceptions here would swallow those control-flow
    signals, since ``typer.Exit`` subclasses ``RuntimeError``.
    """
    app()


if __name__ == "__main__":  # pragma: no cover
    run()
