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
