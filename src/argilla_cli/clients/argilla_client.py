from __future__ import annotations

from typing import Optional

import argilla as rg

from argilla_cli.settings import Settings


def get_client(settings: Settings) -> rg.Argilla:
    """Create an Argilla client using provided settings."""
    return rg.Argilla(
        api_url=str(settings.argilla_api_url),
        api_key=settings.argilla_api_key,
    )


def check_connectivity(client: rg.Argilla) -> tuple[bool, Optional[str]]:
    """Lightweight connectivity/auth check. Returns (ok, error)."""
    try:
        _ = list(client.workspaces)  # touches API
        return True, None
    except Exception as e:  # Classified at CLI layer for user-friendly codes
        return False, str(e)
