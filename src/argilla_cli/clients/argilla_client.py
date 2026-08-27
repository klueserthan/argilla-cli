"""Argilla SDK client construction.

``import argilla`` pulls in a large dependency tree and costs noticeable
startup time, so it happens lazily inside the functions below. That keeps
``--help``, ``config show`` and shell completion fast and, more importantly,
working even when the SDK cannot be imported.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from argilla_cli.errors import (
    AuthConfigError,
    CLIError,
    NetworkApiError,
    NotFoundError,
    ValidationError,
    map_exception,
)
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


def http_transport(client: Any) -> Any | None:
    """Locate the SDK's HTTP client.

    ``APIClient.__init__`` sets ``self.http_client`` and then builds
    ``self.api`` from it, so the direct attribute is the primary lookup. The
    ``client.api`` fallback costs one ``getattr`` and means a future
    reshuffle of where the transport hangs degrades into "still works"
    rather than "server info silently reports no transport".
    """
    for candidate in (
        getattr(client, "http_client", None),
        getattr(getattr(client, "api", None), "http_client", None),
    ):
        if candidate is not None:
            return candidate
    return None


#: How informative a probe failure is, lowest first. When every endpoint
#: fails, the one that best explains *why* is reported.
#:
#: A 404 ranks last on purpose: these endpoints are probed speculatively and
#: may simply not exist on a given server, so "not found" says the least of
#: any outcome. Reporting whichever failure happened to come last meant a
#: 401 on `/version` followed by a 404 on `/status` surfaced as exit 12 --
#: "no such thing" -- when the real answer was "your credentials are wrong".
_PROBE_ERROR_RANK: dict[type[CLIError], int] = {
    AuthConfigError: 0,
    NetworkApiError: 1,
    ValidationError: 2,
    CLIError: 3,
    NotFoundError: 4,
}


def _more_informative(candidate: Exception, incumbent: Exception | None) -> bool:
    """True when ``candidate`` explains a probe failure better."""
    if incumbent is None:
        return True
    rank = _PROBE_ERROR_RANK.get(type(map_exception(candidate)), 3)
    current = _PROBE_ERROR_RANK.get(type(map_exception(incumbent)), 3)
    return rank < current


def server_info(client: Any) -> dict[str, Any]:
    """Fetch server version/status via the SDK's underlying HTTP client."""
    http = http_transport(client)
    if http is None:
        raise NetworkApiError("client does not expose an HTTP transport")

    info: dict[str, Any] = {}
    best_error: Exception | None = None
    for label, path in (("version", "/api/v1/version"), ("status", "/api/v1/status")):
        try:
            response = http.get(path)
            response.raise_for_status()
            try:
                payload = response.json()
            except Exception as exc:
                # A 200 whose body is not JSON means something answered that
                # is not the Argilla API -- typically a proxy serving an HTML
                # login or error page. The decode error itself comes from the
                # `json` module, which the classifier has no reason to know,
                # so it reached the unclassified exit 1. A server that
                # responds with nonsense is a server problem: 11.
                raise NetworkApiError(f"{path} did not return JSON: {exc}") from exc
        except Exception as exc:
            # Endpoints are probed opportunistically -- one may be absent on
            # older servers -- but the failures are kept so that a total
            # failure is reported with its real cause rather than a blanket
            # network error, and so that an auth or availability problem is
            # not masked by a 404 from the endpoint probed after it.
            if _more_informative(exc, best_error):
                best_error = exc
            continue
        if isinstance(payload, dict):
            info.update({f"{label}.{k}": v for k, v in payload.items()})
        else:
            info[label] = payload

    if not info:
        if best_error is not None:
            raise best_error
        raise NetworkApiError("server did not report version or status information")
    return info
