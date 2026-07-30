"""Layered settings resolution with source tracking.

Precedence, highest first:

1. explicit CLI flags (``--api-url`` / ``--api-key``)
2. process environment
3. the selected profile in ``config.toml``
4. a local ``.env`` file

Resolution never raises for *missing* credentials -- that would make
``config show``, the command whose job is diagnosing missing credentials,
unable to run. Commands that actually need to reach the server call
``require_credentials`` instead.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values, find_dotenv
from pydantic import AnyHttpUrl, BaseModel, TypeAdapter, field_validator
from pydantic import ValidationError as PydanticValidationError

from argilla_cli.errors import AuthConfigError, ValidationError
from argilla_cli.profiles import PROFILE_KEYS, load_store

#: Reuses pydantic's URL grammar without forcing the field to be required.
_HTTP_URL = TypeAdapter(AnyHttpUrl)

REQUIRED_ENV_VARS = ("ARGILLA_API_URL", "ARGILLA_API_KEY")
OPTIONAL_ENV_VARS = ("HF_TOKEN",)

#: setting name -> environment variable
ENV_VARS: dict[str, str] = dict(PROFILE_KEYS)

SOURCE_FLAG = "flag"
SOURCE_ENV = "env"
SOURCE_DOTENV = ".env"
SOURCE_UNSET = "unset"


def _mask_middle(s: str, show_prefix: int = 4, show_suffix: int = 3) -> str:
    if len(s) <= show_prefix + show_suffix:
        return "*" * len(s)
    return f"{s[:show_prefix]}****{s[-show_suffix:]}"


def mask_token(token: str | None) -> str | None:
    """Mask a secret for display, keeping a recognizable prefix."""
    if token is None:
        return None
    if token.startswith("hf_"):
        return f"hf_{_mask_middle(token[3:])}"
    return _mask_middle(token)


class Settings(BaseModel):
    """Effective configuration. All fields optional so partial config renders."""

    api_url: str | None = None
    api_key: str | None = None
    hf_token: str | None = None
    default_output_dir: Path = Path()

    @field_validator("api_url")
    @classmethod
    def _check_url(cls, value: str | None) -> str | None:
        """Validate against ``AnyHttpUrl`` but keep the caller's spelling.

        Making this field optional meant dropping the ``AnyHttpUrl``
        annotation, and a hand-rolled scheme-prefix check let genuinely
        malformed URLs (an empty host, an out-of-range port) through to
        client construction, where they failed as an unclassified exit 1 and
        made ``config show`` report broken config as fine. Validation is
        delegated back to pydantic; the original string is returned so the
        URL is not silently rewritten with a trailing slash.
        """
        if value is None:
            return None
        value = value.strip().rstrip("/")
        if not value.startswith(("http://", "https://")):
            raise ValueError("must start with http:// or https://")
        try:
            _HTTP_URL.validate_python(value)
        except PydanticValidationError as exc:
            detail = exc.errors()[0]["msg"] if exc.errors() else "invalid URL"
            raise ValueError(detail.removeprefix("Value error, ")) from exc
        return value

    @property
    def argilla_api_url(self) -> str | None:
        """Alias kept for readability at call sites."""
        return self.api_url

    @property
    def argilla_api_key(self) -> str | None:
        return self.api_key


@dataclass
class SettingsInfo:
    """Resolved settings plus where each value came from."""

    settings: Settings
    sources: dict[str, str] = field(default_factory=dict)
    profile: str | None = None
    config_path: Path | None = None
    config_exists: bool = False
    used_dotenv: bool = False
    dotenv_path: str | None = None

    def source_of(self, key: str) -> str:
        return self.sources.get(key, SOURCE_UNSET)


def _describe_invalid(exc: PydanticValidationError, sources: dict[str, str]) -> str:
    """Turn a pydantic error into one terse line naming the offending source.

    Users see "invalid api_url (from env): must start with http://", not a
    multi-line pydantic dump with a docs URL.
    """
    parts: list[str] = []
    for error in exc.errors():
        field = str(error["loc"][0]) if error["loc"] else "configuration"
        message = error["msg"].removeprefix("Value error, ")
        origin = sources.get(field, SOURCE_UNSET)
        parts.append(f"invalid {field} (from {origin}): {message}")
    return "; ".join(parts)


def load_settings(
    *,
    profile: str | None = None,
    api_url: str | None = None,
    api_key: str | None = None,
) -> SettingsInfo:
    """Resolve settings across all layers, recording the winning source."""
    overrides = {"api_url": api_url, "api_key": api_key}

    store = load_store()
    profile_name, profile_values = store.values_for(profile)

    dotenv_path = find_dotenv(usecwd=True) or None
    dotenv_values_map: dict[str, str | None] = (
        dotenv_values(dotenv_path) if dotenv_path else {}
    )

    resolved: dict[str, str] = {}
    sources: dict[str, str] = {}
    used_dotenv = False

    for key, env_var in ENV_VARS.items():
        override = overrides.get(key)
        if override:
            resolved[key] = override
            sources[key] = SOURCE_FLAG
            continue

        from_env = os.environ.get(env_var)
        if from_env:
            resolved[key] = from_env
            sources[key] = SOURCE_ENV
            continue

        from_profile = profile_values.get(key)
        if from_profile:
            resolved[key] = from_profile
            sources[key] = f"profile:{profile_name}"
            continue

        from_dotenv = dotenv_values_map.get(env_var)
        if from_dotenv:
            resolved[key] = from_dotenv
            sources[key] = SOURCE_DOTENV
            used_dotenv = True
            continue

        sources[key] = SOURCE_UNSET

    payload: dict[str, object] = {
        k: v for k, v in resolved.items() if k != "default_output_dir"
    }
    if "default_output_dir" in resolved:
        payload["default_output_dir"] = Path(resolved["default_output_dir"])
    else:
        payload["default_output_dir"] = Path.cwd()

    try:
        settings = Settings(**payload)  # type: ignore[arg-type]
    except PydanticValidationError as exc:
        raise ValidationError(_describe_invalid(exc, sources)) from exc

    return SettingsInfo(
        settings=settings,
        sources=sources,
        profile=profile_name,
        config_path=store.path,
        config_exists=store.exists,
        used_dotenv=used_dotenv,
        dotenv_path=dotenv_path if used_dotenv else None,
    )


def validate_value(key: str, value: str) -> None:
    """Check one setting before it is written to a profile.

    ``config set`` used to persist whatever it was given and report success,
    so an invalid URL was only discovered by a later command -- leaving a
    profile that looks configured but cannot connect. Failing at the point of
    entry keeps the stored config always loadable.
    """
    try:
        Settings(**{key: value})  # type: ignore[arg-type]
    except PydanticValidationError as exc:
        detail = exc.errors()[0]["msg"] if exc.errors() else "invalid value"
        raise ValidationError(
            f"invalid value for {key}: {detail.removeprefix('Value error, ')}"
        ) from exc


def require_credentials(info: SettingsInfo) -> Settings:
    """Return settings, or raise a helpful ``AuthConfigError`` if incomplete."""
    missing = [
        env_var
        for key, env_var in (
            ("api_url", "ARGILLA_API_URL"),
            ("api_key", "ARGILLA_API_KEY"),
        )
        if not getattr(info.settings, key)
    ]
    if missing:
        raise AuthConfigError(
            f"missing credentials: {', '.join(missing)}. "
            "Set them in the environment, or run: "
            "argilla-cli config set api_url <url> && "
            "argilla-cli config set api_key <key>"
        )
    return info.settings
