"""Reusable option and argument definitions.

Every command imports its options from here so that a flag is spelled,
documented, and defaulted identically across the whole CLI. Global flags
(``--output``, ``--verbose``, ``--profile``, ``--yes``) are declared once on
the root callback in :mod:`argilla_cli.main` and are deliberately *not*
repeated per command.
"""

from __future__ import annotations

from typing import Annotated

import typer

from argilla_cli.context import ctx

WorkspaceOpt = Annotated[
    str | None,
    typer.Option(
        "--workspace",
        "-w",
        help="Workspace name. Defaults to the global --workspace if set.",
    ),
]

LimitOpt = Annotated[
    int | None,
    typer.Option("--limit", "-n", min=1, help="Maximum number of records."),
]


def confirm(action: str) -> None:
    """Prompt before a destructive action unless ``--yes`` was passed."""
    if ctx.assume_yes:
        return
    typer.confirm(action, abort=True)


def resolve_workspace_name(workspace: str | None) -> str | None:
    """Command-level workspace, falling back to the global default."""
    return workspace or ctx.default_workspace
