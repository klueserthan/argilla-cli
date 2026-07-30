"""Persistent, profile-based configuration.

Profiles let one install talk to several Argilla servers (say ``prod`` and
``staging``) without juggling environment variables. The store lives at
``$XDG_CONFIG_HOME/argilla-cli/config.toml`` (falling back to
``~/.config``) and looks like::

    current = "prod"

    [profiles.prod]
    api_url = "https://argilla.example.com"
    api_key = "rbga_..."

Environment variables always win over the file, so existing env-only setups
keep working untouched.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import tomli_w

from argilla_cli.errors import NotFoundError, ValidationError

#: Keys a profile is allowed to hold, mapped to the env var they stand in for.
PROFILE_KEYS: dict[str, str] = {
    "api_url": "ARGILLA_API_URL",
    "api_key": "ARGILLA_API_KEY",
    "hf_token": "HF_TOKEN",
    "default_output_dir": "ARGILLA_DEFAULT_OUTPUT_DIR",
}

DEFAULT_PROFILE = "default"
ENV_CONFIG_PATH = "ARGILLA_CLI_CONFIG"
ENV_PROFILE = "ARGILLA_CLI_PROFILE"


def config_path() -> Path:
    """Location of the config file, honouring XDG and a test/env override."""
    override = os.environ.get(ENV_CONFIG_PATH)
    if override:
        return Path(override).expanduser()
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base).expanduser() if base else Path.home() / ".config"
    return root / "argilla-cli" / "config.toml"


@dataclass
class ProfileStore:
    """In-memory view of the config file."""

    current: str | None = None
    profiles: dict[str, dict[str, str]] = field(default_factory=dict)
    path: Path = field(default_factory=config_path)
    exists: bool = False

    def get(self, name: str) -> dict[str, str]:
        try:
            return self.profiles[name]
        except KeyError:
            raise NotFoundError(f"profile {name!r} not found in {self.path}") from None

    def resolve_name(self, requested: str | None = None) -> str | None:
        """Pick the profile to use: explicit > $ARGILLA_CLI_PROFILE > current."""
        if requested:
            return requested
        from_env = os.environ.get(ENV_PROFILE)
        if from_env:
            return from_env
        return self.current

    def values_for(
        self, requested: str | None = None
    ) -> tuple[str | None, dict[str, str]]:
        """Return (profile_name, values). Missing/unset profiles yield {}."""
        name = self.resolve_name(requested)
        if name is None:
            return None, {}
        if requested or os.environ.get(ENV_PROFILE):
            # An explicitly requested profile that doesn't exist is an error;
            # a stale `current` pointer is not worth failing every command over.
            return name, self.get(name)
        return (name, self.profiles.get(name, {}))


def load_store() -> ProfileStore:
    """Read the config file. A missing file yields an empty store, not an error."""
    path = config_path()
    if not path.is_file():
        return ProfileStore(path=path, exists=False)

    try:
        raw: dict[str, Any] = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ValidationError(f"failed to read config file {path}: {exc}") from exc

    profiles_raw = raw.get("profiles", {})
    if not isinstance(profiles_raw, dict):
        raise ValidationError(f"'profiles' must be a table in {path}")

    profiles: dict[str, dict[str, str]] = {}
    for name, values in profiles_raw.items():
        if not isinstance(values, dict):
            raise ValidationError(f"profile {name!r} must be a table in {path}")
        profiles[name] = {k: str(v) for k, v in values.items() if v is not None}

    current = raw.get("current")
    return ProfileStore(
        current=str(current) if current else None,
        profiles=profiles,
        path=path,
        exists=True,
    )


def save_store(store: ProfileStore) -> None:
    """Write the config file, creating parents and keeping it user-private."""
    payload: dict[str, Any] = {}
    if store.current:
        payload["current"] = store.current
    payload["profiles"] = store.profiles

    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_bytes(tomli_w.dumps(payload).encode("utf-8"))
    # The file holds API keys; don't leave it world-readable.
    try:
        store.path.chmod(0o600)
    except OSError:  # pragma: no cover - platform dependent
        pass


def validate_key(key: str) -> str:
    if key not in PROFILE_KEYS:
        allowed = ", ".join(sorted(PROFILE_KEYS))
        raise ValidationError(f"unknown config key {key!r}. Valid keys: {allowed}")
    return key
