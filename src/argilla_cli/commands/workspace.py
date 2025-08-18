from __future__ import annotations

from typing import Any, List

import typer
import argilla as rg

from argilla_cli.clients.argilla_client import get_client
from argilla_cli.io_utils import emit_json, print_error, print_ok, print_table
from argilla_cli.settings import load_settings
from argilla_cli.errors import exit_with_error
from argilla_cli.globals import state

app = typer.Typer(help="Manage Argilla workspaces")


@app.command("list")
def list_workspaces(
    json_output: bool = typer.Option(False, "--json/--no-json", help="Output JSON"),
) -> None:
    """List all workspaces (name, id, created_at, description).

    Examples:
      argilla-cli workspace list
    """
    try:
        client = get_client(load_settings().settings)
        rows: List[dict[str, Any]] = []
        for ws in client.workspaces:  # type: ignore[attr-defined]
            rows.append(
                {
                    "name": getattr(ws, "name", ""),
                    "id": getattr(ws, "id", ""),
                    "created_at": getattr(ws, "created_at", ""),
                    "description": getattr(ws, "description", ""),
                }
            )
    except Exception as e:
        exit_with_error(e, verbose=state.verbose)
        return

    if state.json_output or json_output:
        emit_json(rows)
    else:
        print_table(rows)


@app.command("create")
def create_workspace(
    name: str = typer.Argument(..., help="Workspace name"),
    exists_ok: bool = typer.Option(
        False, "--exists-ok/--no-exists-ok", help="Succeed if already exists"
    ),
    json_output: bool = typer.Option(False, "--json/--no-json", help="Output JSON"),
) -> None:
    """Create a workspace named NAME.

    If it exists and --exists-ok, exit 0 and print its id; else exit code 12.

    Examples:
      argilla-cli workspace create nlp-lab --exists-ok
    """
    try:
        client = get_client(load_settings().settings)
        # If it exists
        try:
            existing = client.workspaces(name)  # type: ignore[index]
            if existing and exists_ok:
                payload = {
                    "name": getattr(existing, "name", name),
                    "id": getattr(existing, "id", ""),
                }
                if state.json_output or json_output:
                    emit_json(payload)
                else:
                    ident = getattr(existing, "id", "")
                    print_ok(f"Workspace exists: {name} ({ident})")
                raise typer.Exit(code=0)
            elif existing:
                print_error("Workspace already exists.")
                raise typer.Exit(code=12)
        except Exception:
            # Not found is okay; proceed to create
            pass

        ws = rg.Workspace(name=name, client=client)
        ws = ws.create()
    except Exception as e:
        exit_with_error(e, verbose=state.verbose)
        return

    payload = {"name": getattr(ws, "name", name), "id": getattr(ws, "id", "")}
    if state.json_output or json_output:
        emit_json(payload)
    else:
        ident = payload["id"]
        print_ok(f"Created workspace: {name} ({ident})")


@app.command("delete")
def delete_workspace(
    name: str = typer.Argument(..., help="Workspace name"),
    yes: bool = typer.Option(False, "--yes", help="Confirm deletion (no prompt)"),
) -> None:
    """Delete a workspace by NAME.

    Examples:
      argilla-cli workspace delete nlp-lab --yes
    """
    try:
        client = get_client(load_settings().settings)
        ws = client.workspaces(name)  # type: ignore[index]
    except Exception as e:
        exit_with_error(e, verbose=state.verbose)
        return

    if not yes:
        typer.confirm(f"Delete workspace '{name}'?", abort=True)

    try:
        ws.delete()  # type: ignore[attr-defined]
        print_ok(f"Deleted workspace: {name}")
    except Exception as e:
        exit_with_error(e, verbose=state.verbose)
