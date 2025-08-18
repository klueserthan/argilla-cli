from __future__ import annotations

import typer

from argilla_cli.clients.argilla_client import get_client
from argilla_cli.io_utils import emit_json, print_table
from argilla_cli.settings import load_settings

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
