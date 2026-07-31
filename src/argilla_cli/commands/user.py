"""User management commands."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any

import typer

from argilla_cli.context import ctx
from argilla_cli.errors import handle_errors
from argilla_cli.io_utils import print_ok, render
from argilla_cli.options import WorkspaceOpt, confirm, resolve_workspace_name
from argilla_cli.resources import list_users, resolve_user, resolve_workspace

app = typer.Typer(help="Manage Argilla users", no_args_is_help=True)


class UserRole(StrEnum):
    """Roles Argilla accepts, mirrored so bad input fails at the CLI boundary.

    Passing an unconstrained string through to the SDK meant an unsupported
    role surfaced as a pydantic error mapped to a generic exit 1. Declaring
    the choices here makes it a usage error (exit 2) and lists the valid
    values in ``--help``.
    """

    ANNOTATOR = "annotator"
    ADMIN = "admin"
    OWNER = "owner"


_COLUMNS = ["username", "id", "role", "first_name", "last_name"]


def _row(user: Any) -> dict[str, Any]:
    return {
        "username": getattr(user, "username", ""),
        "id": getattr(user, "id", ""),
        "role": getattr(user, "role", ""),
        "first_name": getattr(user, "first_name", ""),
        "last_name": getattr(user, "last_name", ""),
    }


def _workspace_names(user: Any) -> list[str]:
    workspaces = getattr(user, "workspaces", [])
    return sorted(str(getattr(ws, "name", "")) for ws in workspaces)


@app.command("me")
@handle_errors
def me() -> None:
    """Show the currently authenticated user."""
    user = ctx.client().me
    detail = _row(user)
    detail["workspaces"] = _workspace_names(user)
    render(detail)


@app.command("list")
@handle_errors
def list_(workspace: WorkspaceOpt = None) -> None:
    """List all users, optionally filtered to one workspace."""
    rows = [
        _row(user)
        for user in list_users(ctx.client(), resolve_workspace_name(workspace))
    ]
    render(rows, columns=_COLUMNS)


@app.command("show")
@handle_errors
def show(
    username: Annotated[str, typer.Argument(help="Username")],
) -> None:
    """Show one user, including their workspaces."""
    user = resolve_user(ctx.client(), username)
    detail = _row(user)
    detail["workspaces"] = _workspace_names(user)
    render(detail)


@app.command("create")
@handle_errors
def create(
    username: Annotated[str, typer.Argument(help="Username")],
    password: Annotated[
        str,
        typer.Option(..., prompt=True, hide_input=True, help="User password."),
    ],
    role: Annotated[
        UserRole,
        typer.Option("--role", help="User role.", case_sensitive=False),
    ] = UserRole.ANNOTATOR,
    first_name: Annotated[
        str | None, typer.Option("--first-name", help="First name.")
    ] = None,
    last_name: Annotated[
        str | None, typer.Option("--last-name", help="Last name.")
    ] = None,
) -> None:
    """Create a user."""
    import argilla as rg

    client = ctx.client()
    user = rg.User(
        username=username,
        password=password,
        role=role.value,
        first_name=first_name,
        last_name=last_name,
        client=client,
    ).create()
    render(_row(user), columns=_COLUMNS)
    print_ok(f"Created user: {username}")


@app.command("delete")
@handle_errors
def delete(
    username: Annotated[str, typer.Argument(help="Username")],
) -> None:
    """Delete a user."""
    user = resolve_user(ctx.client(), username)
    confirm(f"Delete user '{username}'?")
    user.delete()
    print_ok(f"Deleted user: {username}")


@app.command("add-to-workspace")
@handle_errors
def add_to_workspace(
    username: Annotated[str, typer.Argument(help="Username")],
    workspace: Annotated[str, typer.Argument(help="Workspace name")],
) -> None:
    """Add a user to a workspace."""
    client = ctx.client()
    user = resolve_user(client, username)
    ws = resolve_workspace(client, workspace)
    user.add_to_workspace(ws)
    print_ok(f"Added '{username}' to workspace '{workspace}'")


@app.command("remove-from-workspace")
@handle_errors
def remove_from_workspace(
    username: Annotated[str, typer.Argument(help="Username")],
    workspace: Annotated[str, typer.Argument(help="Workspace name")],
) -> None:
    """Remove a user from a workspace."""
    client = ctx.client()
    user = resolve_user(client, username)
    ws = resolve_workspace(client, workspace)
    confirm(f"Remove '{username}' from workspace '{workspace}'?")
    user.remove_from_workspace(ws)
    print_ok(f"Removed '{username}' from workspace '{workspace}'")
