"""Workspace management commands."""

from __future__ import annotations

from typing import Annotated, Any

import typer

from argilla_cli.context import ctx
from argilla_cli.errors import ValidationError, handle_errors
from argilla_cli.io_utils import print_ok, render
from argilla_cli.options import confirm
from argilla_cli.resources import list_workspaces, resolve_user, resolve_workspace

app = typer.Typer(help="Manage Argilla workspaces", no_args_is_help=True)

_COLUMNS = ["name", "id", "created_at", "description"]


def _row(workspace: Any) -> dict[str, Any]:
    return {
        "name": getattr(workspace, "name", ""),
        "id": getattr(workspace, "id", ""),
        "created_at": getattr(workspace, "created_at", ""),
        "description": getattr(workspace, "description", ""),
    }


@app.command("list")
@handle_errors
def list_() -> None:
    """List all workspaces."""
    rows = [_row(ws) for ws in list_workspaces(ctx.client())]
    render(rows, columns=_COLUMNS)


@app.command("show")
@handle_errors
def show(
    name: Annotated[str, typer.Argument(help="Workspace name")],
) -> None:
    """Show one workspace, including its datasets and members."""
    client = ctx.client()
    workspace = resolve_workspace(client, name)

    detail = _row(workspace)
    detail["datasets"] = sorted(
        str(getattr(ds, "name", "")) for ds in getattr(workspace, "datasets", [])
    )
    detail["users"] = sorted(
        str(getattr(user, "username", "")) for user in getattr(workspace, "users", [])
    )
    render(detail)


@app.command("create")
@handle_errors
def create(
    name: Annotated[str, typer.Argument(help="Workspace name")],
    exists_ok: Annotated[
        bool,
        typer.Option("--exists-ok", help="Succeed if the workspace already exists."),
    ] = False,
) -> None:
    """Create a workspace.

    With ``--exists-ok`` an existing workspace is reported and the command
    exits 0; without it, exit 13.
    """
    import argilla as rg

    client = ctx.client()

    existing = client.workspaces(name)
    if existing is not None:
        if not exists_ok:
            raise ValidationError(f"workspace {name!r} already exists")
        render(_row(existing), columns=_COLUMNS)
        print_ok(f"Workspace already exists: {name}")
        return

    workspace = rg.Workspace(name=name, client=client).create()
    render(_row(workspace), columns=_COLUMNS)
    print_ok(f"Created workspace: {name}")


@app.command("delete")
@handle_errors
def delete(
    name: Annotated[str, typer.Argument(help="Workspace name")],
) -> None:
    """Delete a workspace."""
    workspace = resolve_workspace(ctx.client(), name)
    confirm(f"Delete workspace '{name}'?")
    workspace.delete()
    print_ok(f"Deleted workspace: {name}")


@app.command("users")
@handle_errors
def users(
    name: Annotated[str, typer.Argument(help="Workspace name")],
) -> None:
    """List the members of a workspace."""
    workspace = resolve_workspace(ctx.client(), name)
    rows = [
        {
            "username": getattr(user, "username", ""),
            "id": getattr(user, "id", ""),
            "role": getattr(user, "role", ""),
        }
        for user in getattr(workspace, "users", [])
    ]
    render(rows, columns=["username", "id", "role"])


@app.command("add-user")
@handle_errors
def add_user(
    name: Annotated[str, typer.Argument(help="Workspace name")],
    username: Annotated[str, typer.Argument(help="Username to add")],
) -> None:
    """Add a user to a workspace."""
    client = ctx.client()
    workspace = resolve_workspace(client, name)
    user = resolve_user(client, username)
    workspace.add_user(user)
    print_ok(f"Added '{username}' to workspace '{name}'")


@app.command("remove-user")
@handle_errors
def remove_user(
    name: Annotated[str, typer.Argument(help="Workspace name")],
    username: Annotated[str, typer.Argument(help="Username to remove")],
) -> None:
    """Remove a user from a workspace."""
    client = ctx.client()
    workspace = resolve_workspace(client, name)
    user = resolve_user(client, username)
    confirm(f"Remove '{username}' from workspace '{name}'?")
    workspace.remove_user(user)
    print_ok(f"Removed '{username}' from workspace '{name}'")
