"""Assumptions this CLI makes about the Argilla SDK, checked against it.

Every other test runs against a fake client, which is fast and hermetic but
cannot notice when the real SDK's shape drifts away from what the fake
imitates -- or when the fake was wrong to begin with. These tests introspect
the installed `argilla` package directly, so a mismatch fails here instead of
in the field.

They need no server: only the class shapes are examined.
"""

from __future__ import annotations

import inspect


def test_records_accessor_is_iterable() -> None:
    """`dataset.records` is iterable, which is how streaming reads records.

    `iter_dataset_records` relies on this rather than on calling the
    accessor, so that it depends on the narrowest possible contract.
    """
    from argilla.records._dataset_records import DatasetRecords

    assert hasattr(DatasetRecords, "__iter__")


def test_iterating_records_yields_a_lazy_paginating_iterator() -> None:
    """Iteration must page on demand, not materialise the dataset.

    This is what makes `--limit N` cheap. If the SDK ever returned an
    eager sequence instead, the limit would quietly become a post-filter
    over a full download again.
    """
    from argilla.records._dataset_records import (
        DatasetRecords,
        DatasetRecordsIterator,
    )

    signature = inspect.signature(DatasetRecords.__iter__)
    assert signature.return_annotation is not list

    # The iterator fetches a batch at a time from within __next__.
    assert hasattr(DatasetRecordsIterator, "__next__")
    assert hasattr(DatasetRecordsIterator, "_fetch_next_batch")
    source = inspect.getsource(DatasetRecordsIterator.__next__)
    assert "_fetch_next_batch" in source


def test_records_to_list_accepts_flatten() -> None:
    """The flatten path depends on `to_list(flatten=...)` existing."""
    from argilla.records._dataset_records import DatasetRecords

    assert "flatten" in inspect.signature(DatasetRecords.to_list).parameters


def test_records_expose_a_dict_conversion() -> None:
    """`_as_dict` converts SDK records through one of these methods."""
    from argilla.records._resource import Record

    assert any(
        callable(getattr(Record, attr, None))
        for attr in ("to_dict", "model_dump", "dict")
    )


def test_log_is_an_upsert_keyed_on_id() -> None:
    """`copy` strips record ids because logging a known id *updates* it.

    If this ever stopped being an upsert, stripping ids would become
    unnecessary rather than essential -- worth knowing either way.
    """
    from argilla.records._dataset_records import DatasetRecords

    doc = inspect.getdoc(DatasetRecords.log) or ""
    assert "id" in doc
    assert "updated" in doc.lower()


def test_api_errors_carry_a_status_code() -> None:
    """Error classification reads `.status_code` off SDK exceptions."""
    from argilla._exceptions._api import ArgillaAPIError

    error = ArgillaAPIError("boom", status_code=404)
    assert error.status_code == 404


def test_accessors_return_none_for_a_missing_resource() -> None:
    """`resources.py` turns a `None` return into a clean not-found error.

    The SDK signals absence by returning None rather than raising, which is
    what the original `workspace delete` crash came from. If these ever
    became non-optional, the None-checks would be dead code.
    """
    from argilla.client import Datasets, Users, Workspaces

    for accessor in (Workspaces, Datasets, Users):
        returns = inspect.signature(accessor.__call__).return_annotation
        assert "Optional" in str(returns), f"{accessor.__name__} no longer returns None"


def test_dataset_from_hub_persists_the_dataset() -> None:
    """`from-hub` deliberately does not call `.create()` itself.

    The SDK does it, on both branches reachable with settings="auto".
    """
    from argilla.datasets._resource import Dataset

    source = inspect.getsource(Dataset.from_hub)
    assert "from_disk" in source
    assert "dataset.create()" in inspect.getsource(Dataset.from_disk)


def test_user_roles_match_the_cli_enum() -> None:
    """The CLI mirrors the SDK's roles; drift would reject a valid role."""
    from argilla._models._user import Role

    from argilla_cli.commands.user import UserRole

    assert {r.value for r in UserRole} == {r.value for r in Role}


def test_per_record_flattener_is_available() -> None:
    """Lazy `--flatten` uses the SDK's own per-record flattener.

    `to_list(flatten=True)` is a loop over this function, so applying it to a
    streamed record yields identical rows. It is private, so the CLI falls
    back to the eager path if it disappears -- this test makes that
    degradation visible rather than silent.
    """
    from argilla.records._io._generic import GenericIO

    assert callable(GenericIO._record_to_dict)
    parameters = inspect.signature(GenericIO._record_to_dict).parameters
    assert "flatten" in parameters


def test_client_exposes_the_http_transport_directly() -> None:
    """`server info` reads the transport off the client itself.

    APIClient.__init__ assigns `self.http_client` and then builds `self.api`
    from it, so the direct attribute is the real one. Asserted against the
    SDK because the fake client in the other tests attaches `http_client`
    by hand -- it would look identical whether or not this held.
    """
    from argilla._api._client import APIClient

    source = inspect.getsource(APIClient.__init__)
    assert "self.http_client" in source
    # ...and `api` is derived from it, not the other way round.
    assert "ArgillaAPI(self.http_client)" in source


def test_transport_lookup_finds_either_location() -> None:
    """The lookup tolerates the transport moving under `client.api`."""
    from argilla_cli.clients.argilla_client import http_transport

    class Direct:
        http_client = "transport"

    class ViaApi:
        class api:  # noqa: N801 - mimicking an attribute namespace
            http_client = "transport"

    class Neither:
        pass

    assert http_transport(Direct()) == "transport"
    assert http_transport(ViaApi()) == "transport"
    assert http_transport(Neither()) is None


def test_log_validates_every_record_before_uploading_any() -> None:
    """`push` checks record shapes up front because `log()` does the same.

    `_ingest_records` runs over the whole argument before the chunked upload
    loop starts, which is why calling `log()` once per batch moved the check
    later than it used to be. The CLI now runs the same ingestion across the
    full input first; if this ordering changed, that pass would be either
    redundant or wrong.
    """
    from argilla.records._dataset_records import DatasetRecords

    source = inspect.getsource(DatasetRecords.log)
    ingest_at = source.index("_ingest_records")
    upload_at = source.index("bulk_upsert")
    assert ingest_at < upload_at, "log() no longer validates before uploading"


def test_ingestion_hook_is_available_for_the_early_shape_check() -> None:
    """The private hook `push` borrows to validate before its first upload.

    Private, so the CLI degrades to "no early shape check" if it disappears.
    This test makes that degradation visible rather than silent.
    """
    from argilla.records._dataset_records import DatasetRecords

    assert callable(getattr(DatasetRecords, "_ingest_records", None))


def test_the_client_carries_an_authenticated_httpx_transport() -> None:
    """`annotation_api` sends its own requests down the SDK's own client.

    The whole seam rests on this: the transport is a real `httpx.Client`
    already carrying the base URL and the API-key header, so reaching an
    endpoint the SDK never wrapped costs no second credential path. If it
    ever stopped being an `httpx.Client`, `raise_for_status` and the
    `httpx.HTTPStatusError` the exit codes are read from would go with it.

    The transport is built through the SDK's own factory rather than by
    constructing `Argilla`, which validates the connection in `__init__` and
    would need a live server -- these tests deliberately need none.
    """
    import httpx
    from argilla._api import create_http_client
    from argilla._api._client import APIClient

    # ...and that factory is the one the client uses for the attribute the
    # seam reads, so asserting on its product asserts on the real thing.
    assert "self.http_client = create_http_client(" in inspect.getsource(
        APIClient.__init__
    )

    transport = create_http_client(
        api_url="https://argilla.example.com",
        api_key="a-key",
        timeout=60,
        retries=0,
    )

    assert isinstance(transport, httpx.Client)
    assert str(transport.base_url).startswith("https://argilla.example.com")
    assert transport.headers["X-Argilla-Api-Key"] == "a-key"


def test_the_sdk_only_searches_records_through_the_admin_route() -> None:
    """Why `annotation_api` exists at all.

    `dataset.records` reaches the server through `RecordsAPI.search`, which
    posts to `/api/v1/datasets/{id}/records/search` -- owner/admin only,
    because it returns every user's responses. There is no `/me` equivalent
    anywhere in the records API. The day one appears, this test fails and the
    hand-rolled request should be replaced by it.

    Read off the class rather than off `search`: every method is wrapped by
    `api_error_handler`, which does not use `functools.wraps`, so the
    per-method source is the wrapper's.
    """
    from argilla._api._records import RecordsAPI

    source = inspect.getsource(RecordsAPI)
    assert "/api/v1/datasets/{dataset_id}/records/search" in source
    assert "me/datasets" not in source


def test_the_sdk_response_wrapper_insists_on_a_user_id() -> None:
    """Why the response POST is hand-rolled rather than delegated.

    `RecordsAPI.create_record_response` posts to the same URL this CLI does,
    but only through `UserResponseModel`, which warns when `user_id` is
    absent and then serialises the absent one as the string `"None"`. On
    `/api/v1/records/{id}/responses` the server binds the response to the
    authenticated caller, so there is no user id for an annotator to supply
    and nothing useful to do about the warning. The extra key survives only
    because the server's schema ignores unknown fields.
    """
    import warnings

    from argilla._models._record._response import UserResponseModel

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        model = UserResponseModel(values={"label": {"value": "x"}}, status="submitted")

    assert any("user_id" in str(w.message) for w in caught)
    assert model.model_dump()["user_id"] == "None"


def test_workspace_membership_takes_a_user_or_a_username() -> None:
    """`workspace add-user`/`remove-user` hand the SDK a `User` resource.

    Review twice reported that these should pass `user.id` instead. They
    should not, and the accessor is explicit about why: it branches on
    `isinstance(user, str)` to mean *username*, and otherwise calls a method
    on the object. A `UUID` is neither, so passing `user.id` would reach the
    `else` branch and fail on a type with no such method.

    Pinned here because the fake client's `add_user(user: FakeUser)` would
    look identical whichever the SDK actually wanted.
    """
    from argilla.workspaces._resource import Workspace, WorkspaceUsers

    for name in ("add_user", "remove_user"):
        annotation = str(inspect.signature(getattr(Workspace, name)).parameters["user"])
        assert "User" in annotation and "str" in annotation, annotation

    for name, delegate in (
        ("add", "add_to_workspace"),
        ("delete", "remove_from_workspace"),
    ):
        source = inspect.getsource(getattr(WorkspaceUsers, name))
        assert "isinstance(user, str)" in source, source
        assert delegate in source, source


def test_workspace_has_no_delete_user_method() -> None:
    """The suggested `workspace.delete_user(user.id)` does not exist.

    Kept as a test rather than a reply so the refutation is checked against
    the SDK on every run instead of resting on one reading of it.
    """
    from argilla.workspaces._resource import Workspace

    assert not hasattr(Workspace, "delete_user")
    assert callable(getattr(Workspace, "remove_user", None))
