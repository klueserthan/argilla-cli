"""The annotator-grade HTTP seam, checked against a scripted transport.

These tests pin the *wire* shape, not just the return value: the endpoints in
``annotation_api`` are hand-rolled because the SDK does not wrap them, so
nothing else would notice if the request body drifted away from what the
server's ``SearchRecordsQuery`` and ``ResponseCreate`` schemas accept. A
server rejecting the body is a 422 the CLI cannot recover from, and it would
only ever show up against a live Argilla.

The transport is a real ``httpx.Client`` over ``httpx.MockTransport``, so
``raise_for_status`` is httpx's own and a scripted 403 arrives as the same
``httpx.HTTPStatusError`` a live server would raise -- which is what
``map_exception`` classifies into the documented exit codes.
"""

from __future__ import annotations

import json as jsonlib

import httpx
import pytest

from argilla_cli.annotation_api import search_my_records, submit_response
from argilla_cli.errors import (
    AuthConfigError,
    NetworkApiError,
    NotFoundError,
    map_exception,
)

from .conftest import FakeAPI, FakeArgilla

DATASET_ID = "11111111-1111-1111-1111-111111111111"
RECORD_ID = "22222222-2222-2222-2222-222222222222"

SEARCH_PATH = f"/api/v1/me/datasets/{DATASET_ID}/records/search"
RESPONSES_PATH = f"/api/v1/records/{RECORD_ID}/responses"

A_SEARCH_RESULT = {
    "items": [
        {
            "record": {
                "id": RECORD_ID,
                "status": "pending",
                "fields": {"text": "hello"},
                "responses": [],
                "suggestions": [{"question_name": "label", "value": "positive"}],
            },
            "query_score": None,
        }
    ],
    "total": 7,
}


def _body(request: httpx.Request) -> dict:
    return jsonlib.loads(request.content.decode("utf-8"))


def test_searching_my_records_posts_to_the_me_endpoint(
    fake_client: FakeArgilla,
    fake_api: FakeAPI,
) -> None:
    """The annotator-accessible route is the ``/me`` one, not ``/datasets``.

    ``POST /api/v1/datasets/{id}/records/search`` -- what the SDK uses -- is
    owner/admin only server-side, so an annotator key gets a 403 from it.
    """
    fake_api.route("POST", SEARCH_PATH, json=A_SEARCH_RESULT)

    search_my_records(fake_client, DATASET_ID, limit=5)

    request = fake_api.requests[-1]
    assert request.method == "POST"
    assert request.url.path == SEARCH_PATH


def test_the_search_asks_for_responses_and_suggestions(
    fake_client: FakeArgilla,
    fake_api: FakeAPI,
) -> None:
    """Both relationships are needed to annotate: the suggestion to accept,
    and the caller's own response to know it is already answered."""
    fake_api.route("POST", SEARCH_PATH, json=A_SEARCH_RESULT)

    search_my_records(fake_client, DATASET_ID, limit=5, offset=10)

    params = fake_api.requests[-1].url.params
    assert sorted(params.get_list("include")) == ["responses", "suggestions"]
    assert params["limit"] == "5"
    assert params["offset"] == "10"


def test_the_offset_defaults_to_the_start(
    fake_client: FakeArgilla, fake_api: FakeAPI
) -> None:
    fake_api.route("POST", SEARCH_PATH, json=A_SEARCH_RESULT)

    search_my_records(fake_client, DATASET_ID, limit=1)

    assert fake_api.requests[-1].url.params["offset"] == "0"


def test_pending_is_a_terms_filter_on_the_response_status_scope(
    fake_client: FakeArgilla,
    fake_api: FakeAPI,
) -> None:
    """The exact filter JSON the server's ``SearchRecordsQuery`` accepts.

    Pinned literally because there is no schema on this side to catch a typo:
    ``entity`` discriminates the scope union, ``property: "status"`` is the
    only property a response scope allows, and the search engine binds the
    scope to the calling user itself -- which is what makes ``pending`` mean
    "not answered *by me*" rather than "not answered by anyone".
    """
    fake_api.route("POST", SEARCH_PATH, json=A_SEARCH_RESULT)

    search_my_records(fake_client, DATASET_ID, limit=5)

    assert _body(fake_api.requests[-1]) == {
        "filters": {
            "and": [
                {
                    "type": "terms",
                    "scope": {"entity": "response", "property": "status"},
                    "values": ["pending"],
                }
            ]
        }
    }


def test_asking_for_every_record_sends_no_filters(
    fake_client: FakeArgilla, fake_api: FakeAPI
) -> None:
    """An empty query is valid: every field of ``SearchRecordsQuery`` is
    optional, and an empty ``filters.and`` would be rejected (min length 1)."""
    fake_api.route("POST", SEARCH_PATH, json=A_SEARCH_RESULT)

    search_my_records(fake_client, DATASET_ID, limit=5, pending_only=False)

    assert _body(fake_api.requests[-1]) == {}


def test_the_search_result_is_unwrapped_into_records_and_a_total(
    fake_client: FakeArgilla,
    fake_api: FakeAPI,
) -> None:
    """Each item wraps the record next to its query score; callers want the
    records, and the total to page with."""
    fake_api.route("POST", SEARCH_PATH, json=A_SEARCH_RESULT)

    records, total = search_my_records(fake_client, DATASET_ID, limit=5)

    assert total == 7
    assert [record["id"] for record in records] == [RECORD_ID]
    assert records[0]["suggestions"] == [
        {"question_name": "label", "value": "positive"}
    ]


def test_an_empty_page_is_not_an_error(
    fake_client: FakeArgilla, fake_api: FakeAPI
) -> None:
    fake_api.route("POST", SEARCH_PATH, json={"items": [], "total": 0})

    assert search_my_records(fake_client, DATASET_ID, limit=5) == ([], 0)


def test_submitting_a_response_wraps_every_value(
    fake_client: FakeArgilla, fake_api: FakeAPI
) -> None:
    """``ResponseValuesCreate`` is a mapping of question name to ``{"value": v}``.

    The user id is deliberately absent: this endpoint binds the response to
    the authenticated caller, and the bulk-upsert route that *does* take a
    user id is admin-only.
    """
    fake_api.route("POST", RESPONSES_PATH, json={"id": "r"}, status_code=201)

    submit_response(
        fake_client,
        RECORD_ID,
        values={"label": "positive", "rating": 4},
        status="submitted",
    )

    request = fake_api.requests[-1]
    assert request.method == "POST"
    assert request.url.path == RESPONSES_PATH
    assert _body(request) == {
        "values": {"label": {"value": "positive"}, "rating": {"value": 4}},
        "status": "submitted",
    }


def test_a_discarded_response_carries_no_values(
    fake_client: FakeArgilla, fake_api: FakeAPI
) -> None:
    """Discarding is an answer without content, so ``values`` is omitted
    rather than sent as ``null``."""
    fake_api.route("POST", RESPONSES_PATH, json={"id": "r"}, status_code=201)

    submit_response(fake_client, RECORD_ID, values=None, status="discarded")

    assert _body(fake_api.requests[-1]) == {"status": "discarded"}


def test_a_forbidden_search_surfaces_as_a_classifiable_status_error(
    fake_client: FakeArgilla,
    fake_api: FakeAPI,
) -> None:
    """The seam raises nothing of its own: ``raise_for_status`` hands the
    HTTP status straight to ``map_exception``, which owns the exit codes."""
    fake_api.route("POST", SEARCH_PATH, json={"detail": "no"}, status_code=403)

    with pytest.raises(httpx.HTTPStatusError) as excinfo:
        search_my_records(fake_client, DATASET_ID, limit=5)

    mapped = map_exception(excinfo.value)
    assert isinstance(mapped, AuthConfigError)
    assert mapped.exit_code == 10


def test_a_missing_record_surfaces_as_a_classifiable_status_error(
    fake_client: FakeArgilla,
    fake_api: FakeAPI,
) -> None:
    fake_api.route("POST", RESPONSES_PATH, json={"detail": "gone"}, status_code=404)

    with pytest.raises(httpx.HTTPStatusError) as excinfo:
        submit_response(fake_client, RECORD_ID, values={"label": "x"}, status="draft")

    mapped = map_exception(excinfo.value)
    assert isinstance(mapped, NotFoundError)
    assert mapped.exit_code == 12


def test_a_client_without_a_transport_is_a_network_error(
    fake_client: FakeArgilla,
) -> None:
    """No ``fake_api``, so the fake client exposes no HTTP transport.

    A client with nothing to send the request down is an availability
    problem, not an unclassified crash -- exit 11, the same verdict
    ``server info`` gives for the same condition. Without this the seam would
    fail on ``None.post`` and report exit 1.
    """
    with pytest.raises(NetworkApiError) as excinfo:
        search_my_records(fake_client, DATASET_ID, limit=5)

    assert excinfo.value.exit_code == 11
