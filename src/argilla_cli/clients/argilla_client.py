"""Argilla SDK client construction.

``import argilla`` pulls in a large dependency tree and costs noticeable
startup time, so it happens lazily inside the functions below. That keeps
``--help``, ``config show`` and shell completion fast and, more importantly,
working even when the SDK cannot be imported.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from argilla_cli.errors import NetworkApiError
from argilla_cli.settings import Settings

if TYPE_CHECKING:  # pragma: no cover - typing only
    import argilla as rg


def get_client(settings: Settings) -> rg.Argilla:
    """Create an Argilla client from resolved settings."""
    import argilla as rg

    return rg.Argilla(
        api_url=str(settings.api_url),
        api_key=str(settings.api_key),
    )


def check_connectivity(client: Any) -> tuple[bool, Exception | None]:
    """Lightweight connectivity/auth probe. Returns ``(ok, exception)``.

    The original exception is returned rather than its message: Argilla's API
    errors carry the status code that ``map_exception`` classifies on, so
    stringifying here would collapse a 401 and a 503 into the same generic
    failure and lose the documented 10-vs-11 exit codes.
    """
    try:
        list(client.workspaces)
        return True, None
    except Exception as exc:  # classified by the caller into an exit code
        return False, exc


def server_info(client: Any) -> dict[str, Any]:
    """Fetch server version/status via the SDK's underlying HTTP client."""
    http = getattr(client, "http_client", None)
    if http is None:
        raise NetworkApiError("client does not expose an HTTP transport")

    info: dict[str, Any] = {}
    last_error: Exception | None = None
    for label, path in (("version", "/api/v1/version"), ("status", "/api/v1/status")):
        try:
            response = http.get(path)
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            # Endpoints are probed opportunistically -- one may be absent on
            # older servers -- but the failure is kept so that a total
            # failure can be reported with its real cause rather than a
            # blanket network error. A 401 here is an auth problem (10).
            last_error = exc
            continue
        if isinstance(payload, dict):
            info.update({f"{label}.{k}": v for k, v in payload.items()})
        else:
            info[label] = payload

    if not info:
        if last_error is not None:
            raise last_error
        raise NetworkApiError("server did not report version or status information")
    return info
