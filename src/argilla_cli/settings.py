from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Optional

from dotenv import find_dotenv, load_dotenv
from pydantic import AnyHttpUrl
from pydantic_settings import BaseSettings, SettingsConfigDict


REQUIRED_ENV_VARS = ("ARGILLA_API_URL", "ARGILLA_API_KEY")
OPTIONAL_ENV_VARS = ("HF_TOKEN",)


def _mask_middle(s: str, show_prefix: int = 4, show_suffix: int = 3) -> str:
    if len(s) <= show_prefix + show_suffix:
        return "*" * len(s)
    return f"{s[:show_prefix]}****{s[-show_suffix:]}"


def mask_token(token: Optional[str]) -> Optional[str]:
    if token is None:
        return None
    if token.startswith("hf_"):
        # Keep the hf_ prefix visible
        return f"hf_{_mask_middle(token[3:])}"
    return _mask_middle(token)


class Settings(BaseSettings):
    """Application settings loaded from env and optionally from a .env file.

    Environment variables:
      - ARGILLA_API_URL
      - ARGILLA_API_KEY
      - HF_TOKEN (optional)
    """

    argilla_api_url: AnyHttpUrl
    argilla_api_key: str
    hf_token: Optional[str] = None
    default_output_dir: Path = Path.cwd()

    model_config = SettingsConfigDict(env_prefix="", extra="ignore")


class SettingsInfo:
    """Holds settings and metadata about their sources."""

    def __init__(
        self,
        settings: Settings,
        sources: Dict[str, str],
        used_dotenv: bool,
        dotenv_path: Optional[str],
    ):
        self.settings = settings
        self.sources = sources  # e.g., {"ARGILLA_API_URL": "env"|".env"}
        self.used_dotenv = used_dotenv
        self.dotenv_path = dotenv_path


def load_settings() -> SettingsInfo:
    """Load settings with precedence: process env > .env (conditionally).

    Only load a .env if one exists and at least one required var is missing
    from process env. Returns SettingsInfo including variable sources.
    """
    pre_env = dict(os.environ)
    missing_required = [
        var for var in REQUIRED_ENV_VARS if var not in pre_env or not pre_env[var]
    ]

    used_dotenv = False
    dotenv_path: Optional[str] = None
    if missing_required:
        dotenv_path = find_dotenv(usecwd=True)
        if dotenv_path:
            # Load only missing variables; override=False keeps existing
            # env values
            load_dotenv(dotenv_path, override=False)
            used_dotenv = True

    settings = Settings()  # type: ignore[call-arg]

    # Determine source for each env var now that .env may be loaded
    sources: Dict[str, str] = {}
    for var in (*REQUIRED_ENV_VARS, *OPTIONAL_ENV_VARS):
        if var in pre_env and pre_env[var]:
            sources[var] = "env"
        elif used_dotenv and os.environ.get(var):
            sources[var] = ".env"
        else:
            sources[var] = "unset"

    return SettingsInfo(
        settings=settings,
        sources=sources,
        used_dotenv=used_dotenv,
        dotenv_path=dotenv_path,
    )
