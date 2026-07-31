"""Configuration and diagnostics commands."""

from __future__ import annotations

from typing import Annotated

import typer

from argilla_cli.clients.argilla_client import check_connectivity
from argilla_cli.context import ctx
from argilla_cli.errors import ValidationError, exit_with_error, handle_errors
from argilla_cli.io_utils import print_ok, print_warn, render
from argilla_cli.options import confirm
from argilla_cli.profiles import (
    DEFAULT_PROFILE,
    PROFILE_KEYS,
    ProfileStore,
    load_store,
    save_store,
    validate_key,
)
from argilla_cli.settings import mask_token, validate_value

app = typer.Typer(
    help="Inspect and manage argilla-cli configuration", no_args_is_help=True
)

_SECRET_KEYS = {"api_key", "hf_token"}


@app.command("show")
@handle_errors
def show() -> None:
    """Show the effective configuration, with secrets masked and sources named.

    Works even when nothing is configured yet -- that is precisely when you
    need it.
    """
    info = ctx.settings_info()
    settings = info.settings

    render(
        {
            "profile": info.profile or "(none)",
            "config_file": str(info.config_path) if info.config_path else "(none)",
            "config_file_exists": info.config_exists,
            "api_url": settings.api_url,
            "api_url_source": info.source_of("api_url"),
            "api_key": mask_token(settings.api_key),
            "api_key_source": info.source_of("api_key"),
            "hf_token": mask_token(settings.hf_token),
            "hf_token_source": info.source_of("hf_token"),
            "default_output_dir": str(settings.default_output_dir),
            "dotenv_path": info.dotenv_path,
        }
    )

    if not settings.api_url or not settings.api_key:
        print_warn(
            "Incomplete configuration. Set credentials with: "
            "argilla-cli config set api_url <url>"
        )


@app.command("doctor")
@handle_errors
def doctor() -> None:
    """Check credentials, server connectivity, and the optional HF token."""
    info = ctx.settings_info()
    ctx.require_settings()

    ok, error = check_connectivity(ctx.client())
    if not ok:
        # Pass the original exception through so an auth failure exits 10 and
        # a server/transport failure exits 11, rather than a generic 1.
        exit_with_error(error or Exception("connectivity check failed"))
    print_ok(f"Argilla connectivity OK ({info.settings.api_url})")

    token = info.settings.hf_token
    if not token:
        print_ok("HF_TOKEN not set (only needed for Hugging Face Hub operations).")
    elif not token.startswith("hf_") or len(token) < 10:
        raise ValidationError("HF_TOKEN looks malformed; expected an 'hf_...' value")
    else:
        print_ok("HF_TOKEN present.")


@app.command("list")
@handle_errors
def list_profiles() -> None:
    """List configured profiles."""
    store = load_store()
    rows = [
        {
            "name": name,
            "current": name == store.current,
            "api_url": values.get("api_url", ""),
            "api_key": mask_token(values.get("api_key")) or "",
        }
        for name, values in sorted(store.profiles.items())
    ]
    render(rows, empty_message=f"(no profiles configured in {store.path})")


def _target_profile(store: ProfileStore, requested: str | None) -> str:
    """Resolve which profile a read/write targets.

    Delegates to the store so the precedence matches everywhere:
    explicit flag > ``$ARGILLA_CLI_PROFILE`` > the current profile. Resolving
    this by hand skipped the environment variable, which meant
    ``ARGILLA_CLI_PROFILE=staging argilla-cli config set api_key ...`` would
    silently write the key into whichever profile happened to be current.
    """
    return store.resolve_name(requested or ctx.profile) or DEFAULT_PROFILE


@app.command("set")
@handle_errors
def set_value(
    key: Annotated[
        str, typer.Argument(help=f"One of: {', '.join(sorted(PROFILE_KEYS))}")
    ],
    value: Annotated[str, typer.Argument(help="Value to store")],
    profile: Annotated[
        str | None,
        typer.Option(
            "--profile", help="Profile to write to. Defaults to the current one."
        ),
    ] = None,
) -> None:
    """Set a configuration value in a profile."""
    validate_key(key)
    validate_value(key, value)
    store = load_store()
    name = _target_profile(store, profile)

    store.profiles.setdefault(name, {})[key] = value
    if store.current is None:
        store.current = name
    save_store(store)

    shown = mask_token(value) if key in _SECRET_KEYS else value
    print_ok(f"Set {key}={shown} in profile '{name}' ({store.path})")


@app.command("get")
@handle_errors
def get_value(
    key: Annotated[str, typer.Argument(help="Configuration key to read")],
    profile: Annotated[str | None, typer.Option("--profile")] = None,
) -> None:
    """Read a single configuration value from a profile."""
    validate_key(key)
    store = load_store()
    name = _target_profile(store, profile)
    values = store.get(name)
    if key not in values:
        raise ValidationError(f"{key!r} is not set in profile {name!r}")
    render({"profile": name, "key": key, "value": values[key]})


@app.command("use")
@handle_errors
def use_profile(
    name: Annotated[str, typer.Argument(help="Profile to make current")],
) -> None:
    """Select the profile used when none is given explicitly."""
    store = load_store()
    store.get(name)  # raises NotFoundError if absent
    store.current = name
    save_store(store)
    print_ok(f"Now using profile '{name}'")


@app.command("remove")
@handle_errors
def remove_profile(
    name: Annotated[str, typer.Argument(help="Profile to delete")],
) -> None:
    """Delete a profile."""
    store = load_store()
    store.get(name)
    confirm(f"Delete profile '{name}'?")
    del store.profiles[name]
    if store.current == name:
        store.current = next(iter(sorted(store.profiles)), None)
    save_store(store)
    print_ok(f"Deleted profile '{name}'")
