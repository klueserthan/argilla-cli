from __future__ import annotations

import typer

from argilla_cli.clients.argilla_client import get_client
from argilla_cli.io_utils import emit_json, print_table
from argilla_cli.settings import load_settings
from argilla_cli.errors import exit_with_error, NotFoundError, ValidationError
from argilla_cli.globals import state
from argilla.users import User

app = typer.Typer(help="User/account commands")


@app.command("me")
def me(
    json_output: bool = typer.Option(False, "--json/--no-json", help="Output JSON"),
) -> None:
    """Show info for the current authenticated user (username, workspaces)."""
    client = get_client(load_settings().settings)
    user = client.me  # type: ignore[attr-defined]
    data = {
        "username": getattr(user, "username", None),
        "first_name": getattr(user, "first_name", None),
        "last_name": getattr(user, "last_name", None),
        "workspaces": [
            getattr(ws, "name", None) for ws in getattr(user, "workspaces", [])
        ],
    }
    if json_output:
        emit_json(data)
    else:
        rows = [{"key": k, "value": v} for k, v in data.items()]
        print_table(rows)


@app.command("create")
def create(
    username: str = typer.Argument(..., help="New user's username"),
    password: str = typer.Argument(..., help="New user's password"),
    role: str = typer.Argument(..., help="Role: admin or annotator"),
    first_name: str | None = typer.Option(None, "--first-name", help="First name"),
    last_name: str | None = typer.Option(None, "--last-name", help="Last name"),
    workspace: str | None = typer.Option(
        None,
        "--workspace",
        "-w",
        help=(
            "If provided, attach the new user to this workspace. "
            "Must exist; otherwise an error is raised."
        ),
    ),
    json_output: bool = typer.Option(False, "--json/--no-json", help="Output JSON"),
) -> None:
    """Create a new user.

    - role must be one of: administrator, annotator
    - If --workspace is given, it must exist and the user will be added to it.

    Examples:
      argilla-cli user create alice annotator --first-name Alice --last-name Smith
      argilla-cli user create bob administrator -w nlp-lab --json
    """
    # Normalize role: accept 'administrator' as 'admin'
    normalized_role = role.strip().lower()
    if normalized_role == "administrator":
        normalized_role = "admin"
    if normalized_role not in {"admin", "annotator"}:
        exit_with_error(
            ValidationError("role must be either 'admin' or 'annotator'"),
            verbose=state.verbose,
        )
        return

    try:
        client = get_client(load_settings().settings)

        # Resolve optional workspace
        ws_obj = None
        if workspace:
            try:
                ws_obj = client.workspaces(workspace)  # type: ignore[index]
            except Exception:
                raise NotFoundError(f"workspace not found: {workspace}")

        # Create user
        user = User(
            username=username,
            password=password,
            first_name=first_name,
            last_name=last_name,
            role=normalized_role,
            client=client,
        )
        user = user.create()

        # Optionally attach to workspace
        if ws_obj is not None:
            user.add_to_workspace(ws_obj)  # type: ignore[arg-type]

        payload = {
            "username": getattr(user, "username", username),
            "first_name": getattr(user, "first_name", first_name),
            "last_name": getattr(user, "last_name", last_name),
            "role": getattr(user, "role", normalized_role),
            "id": getattr(user, "id", None),
            "workspaces": [getattr(ws_obj, "name", None)] if ws_obj is not None else [],
        }
    except Exception as e:
        exit_with_error(e, verbose=state.verbose)
        return

    if state.json_output or json_output:
        emit_json(payload)
    else:
        print_table([payload])
