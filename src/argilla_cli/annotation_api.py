"""Reading and answering records with an *annotator* key.

The SDK's record paths are administrative. ``dataset.records`` iterates by
posting to ``/api/v1/datasets/{id}/records/search``, whose server-side policy
(``search_records_with_all_responses``) admits owners and admins only, and
``records.log()`` goes through the bulk upsert, which is admin-only for the
same reason: both hand back *every* user's responses, and one of them writes
responses on other users' behalf. An annotator key gets a 403 from either,
so every existing command in this CLI is effectively admin-only -- which is
fine for managing a server and useless for doing the annotation work.

The server does expose the annotator-grade equivalents; the SDK simply never
wrapped them, so this module talks to them over the SDK's own authenticated
transport:

* ``POST /api/v1/me/datasets/{dataset_id}/records/search`` -- policy
  ``search_records``, open to any member of the dataset's workspace, and it
  returns only the caller's own responses.
* ``POST /api/v1/records/{record_id}/responses`` -- policy ``create_response``,
  likewise open to any workspace member. The server binds the new response to
  the authenticated caller, which is why no user id is sent: the route that
  takes one is the admin-only bulk upsert.

These are the endpoints the web UI itself uses, so they are as stable as the
annotation product is. That is the argument for hand-rolling them rather than
waiting for the SDK, and it is also the reason the request bodies are pinned
by tests: nothing on this side validates them, so a drifted body would only
fail against a live server, as a 422.

The response route is the one place the SDK comes close: its private
``client.api.records.create_record_response`` posts to the same URL. It is
not used here because it insists on going through ``UserResponseModel``,
which warns when no ``user_id`` is supplied and then serialises the missing
one as the *string* ``"None"``. The server ignores the extra key, so the call
works by accident rather than by contract -- and the warning is addressed to a
caller who cannot do anything about it, because on this route the server is
the thing that knows who is calling. ``test_sdk_contract`` pins both facts.

Nothing here classifies errors. ``raise_for_status`` lets httpx raise, and
``errors.map_exception`` turns the status into the documented exit code
(403 -> 10, 404 -> 12, 5xx -> 11) exactly as it does for the SDK's own
failures. A second opinion here would be a second place deciding exit codes.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from argilla_cli.clients.argilla_client import http_transport
from argilla_cli.errors import NetworkApiError

#: Both relationships every annotation view needs: the suggestion to accept or
#: override, and the caller's own response to know whether it is answered.
#: A tuple, so nothing downstream can quietly drop one from every later call.
SEARCH_INCLUDE = ("responses", "suggestions")


def _pending_filter() -> dict[str, Any]:
    """The "not yet answered by me" filter, as ``SearchRecordsQuery`` wants it.

    ``entity`` discriminates the scope union server-side and ``status`` is the
    only property a response scope accepts. The *by me* part is not expressed
    here and cannot be: the ``/me`` handler binds the scope to the
    authenticated user before it reaches the search engine, which is precisely
    why the same filter on the admin route would mean "answered by nobody".

    Built fresh per call rather than kept as a module constant, so a caller
    that mutates the returned body cannot corrupt the next request.
    """
    return {
        "type": "terms",
        "scope": {"entity": "response", "property": "status"},
        "values": ["pending"],
    }


def _transport(client: Any) -> Any:
    """The SDK's authenticated ``httpx.Client``, or a network error.

    Located through ``clients.argilla_client`` rather than by reaching for
    ``client.api.http_client`` here, so where the transport lives stays one
    decision in one place.
    """
    http = http_transport(client)
    if http is None:
        raise NetworkApiError("client does not expose an HTTP transport")
    return http


def search_my_records(
    client: Any,
    dataset_id: str,
    *,
    limit: int,
    offset: int = 0,
    pending_only: bool = True,
) -> tuple[list[dict[str, Any]], int]:
    """Return one page of records for the caller, and the total matching it.

    Records come back as the server's own JSON rather than SDK objects: the
    responses and suggestions attached to them are what an annotator acts on,
    and round-tripping through the SDK's record model would drop the ones it
    has no admin-side use for.

    ``total`` counts everything matching the query, not the page, so a caller
    can page with it. It is the server's count and can exceed ``limit``.
    """
    query: dict[str, Any] = {}
    if pending_only:
        # `filters.and` has a minimum length of 1 server-side, so "no filter"
        # has to be an absent key rather than an empty list.
        query["filters"] = {"and": [_pending_filter()]}

    response = _transport(client).post(
        f"/api/v1/me/datasets/{dataset_id}/records/search",
        json=query,
        params={"offset": offset, "limit": limit, "include": SEARCH_INCLUDE},
    )
    response.raise_for_status()
    payload = response.json()

    items = payload.get("items") or []
    records = [item["record"] for item in items]
    return records, int(payload.get("total", len(records)))


def submit_response(
    client: Any,
    record_id: str,
    *,
    values: Mapping[str, Any] | None,
    status: str,
) -> None:
    """Answer one record as the calling user.

    ``values`` maps question name to the answer for it; the server's
    ``ResponseValuesCreate`` wraps each one in ``{"value": ...}``. ``None`` is
    for a discard, which is an answer with no content -- the key is omitted
    rather than sent as ``null`` so the body matches the discarded variant of
    the schema exactly.
    """
    body: dict[str, Any] = {}
    if values is not None:
        body["values"] = {name: {"value": value} for name, value in values.items()}
    body["status"] = status

    response = _transport(client).post(
        f"/api/v1/records/{record_id}/responses",
        json=body,
    )
    response.raise_for_status()
