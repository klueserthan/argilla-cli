"""Inspect the Argilla server."""

from __future__ import annotations

import typer

from argilla_cli.clients.argilla_client import check_connectivity, server_info
from argilla_cli.context import ctx
from argilla_cli.errors import NetworkApiError, exit_with_error, handle_errors
from argilla_cli.io_utils import print_ok, render

app = typer.Typer(help="Inspect the Argilla server", no_args_is_help=True)


@app.command("info")
@handle_errors
def info() -> None:
    """Show server version and status information."""
    detail = server_info(ctx.client())
    detail["api_url"] = str(ctx.settings_info().settings.api_url)
    render(detail)


@app.command("whoami")
@handle_errors
def whoami() -> None:
    """Show the current user's username and role."""
    user = ctx.client().me
    render(
        {
            "username": getattr(user, "username", ""),
            "role": getattr(user, "role", ""),
        }
    )


@app.command("health")
@handle_errors
def health() -> None:
    """Check connectivity to the Argilla server."""
    api_url = str(ctx.settings_info().settings.api_url)
    ok, error = check_connectivity(ctx.client())
    if not ok:
        # Let the real exception decide the exit code; a 401 is an auth
        # problem (10), not a network one (11).
        exit_with_error(error or NetworkApiError("server health check failed"))
    print_ok("Server is reachable")
    render({"api_url": api_url, "status": "ok"})
