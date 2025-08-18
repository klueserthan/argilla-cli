from __future__ import annotations

import typer

from argilla_cli.commands import config as config_cmd
from argilla_cli.commands import dataset as dataset_cmd
from argilla_cli.commands import user as user_cmd
from argilla_cli.commands import workspace as workspace_cmd
from argilla_cli.io_utils import print_error
from argilla_cli.globals import state


app = typer.Typer(
    add_completion=False,
    help="argilla-cli: quick access to Argilla from your terminal",
)

# Sub-apps
app.add_typer(workspace_cmd.app, name="workspace")
app.add_typer(dataset_cmd.app, name="dataset")
app.add_typer(user_cmd.app, name="user")
app.add_typer(config_cmd.app, name="config")


@app.callback()
def _global(
    json_output: bool = typer.Option(False, "--json/--no-json", help="Output JSON"),
    verbose: bool = typer.Option(
        False, "--verbose", help="Verbose tracebacks on errors"
    ),
):
    """Global options for the CLI."""
    state.json_output = json_output
    state.verbose = verbose
    return


def run() -> None:  # Entry point for console_scripts
    try:
        app()
    except Exception as e:
        print_error(str(e))
        raise typer.Exit(code=1)


if __name__ == "__main__":  # pragma: no cover
    run()
