from __future__ import annotations

import json
import typer

from argilla_cli.io_utils import emit_json, print_error, print_ok
from argilla_cli.clients.argilla_client import check_connectivity, get_client
from argilla_cli.settings import SettingsInfo, load_settings, mask_token
from argilla_cli.errors import exit_with_error
from argilla_cli.globals import state

app = typer.Typer(help="Configuration commands for argilla-cli")


@app.command("show")
def show(
    json_output: bool = typer.Option(
        False,
        "--json/--no-json",
        help="Output JSON",
    ),
) -> None:
    """Print effective config with masked secrets and their sources.

    Examples:
      argilla-cli config show
      argilla-cli config show --json
    """
    info: SettingsInfo = load_settings()

    data = {
        "argilla_api_url": str(info.settings.argilla_api_url),
        "argilla_api_key": mask_token(info.settings.argilla_api_key),
        "hf_token": mask_token(info.settings.hf_token),
        "default_output_dir": str(info.settings.default_output_dir),
        "sources": info.sources,
        "used_dotenv": info.used_dotenv,
        "dotenv_path": info.dotenv_path,
    }

    if state.json_output or json_output:
        emit_json(data)
    else:
        print_ok("Effective configuration:")
        # Pretty print without exposing full tokens
        safe = {k: v for k, v in data.items()}
        typer.echo(json.dumps(safe, indent=2))


@app.command("doctor")
def doctor() -> None:
    """Validate connectivity to Argilla and check optional HF token.

    Examples:
      argilla-cli config doctor
    """
    info = load_settings()
    client = get_client(info.settings)
    ok, error = check_connectivity(client)
    if not ok:
        exit_with_error(
            Exception(error or "connectivity failed"),
            verbose=state.verbose,
        )
    print_ok("Argilla connectivity: OK")

    token = info.settings.hf_token
    if token:
        if not token.startswith("hf_") or len(token) < 10:
            print_error("HF_TOKEN looks invalid; expected 'hf_...' format.")
            raise typer.Exit(code=10)
        print_ok("HF_TOKEN present.")
    else:
        print_ok("HF_TOKEN not set. Some Hub operations may require it.")
