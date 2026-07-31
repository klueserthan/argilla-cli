"""Regressions for the issues raised in review on PR #1.

Each test pins a behaviour that was wrong when the PR was first opened.
"""

from __future__ import annotations

import csv
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from argilla_cli.errors import map_exception
from argilla_cli.main import app
from argilla_cli.records_io import RecordFormat, read_records, write_records

from .conftest import FakeArgilla, FakeDataset, FakeUser, FakeWorkspace


class _FailingClient(FakeArgilla):
    """A client whose workspace probe fails with a typed API error."""

    def __init__(self, status_code: int) -> None:
        super().__init__()
        self._status_code = status_code

    @property
    def workspaces(self) -> Any:
        raise _ApiError("probe failed", self._status_code)


class _ApiError(Exception):
    """Mimics an Argilla API exception, which carries its status code."""

    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


def _use_client(monkeypatch: pytest.MonkeyPatch, client: Any) -> None:
    from argilla_cli.clients import argilla_client

    monkeypatch.setattr(argilla_client, "get_client", lambda settings: client)


# ---------------------------------------------------------------------------
# Structured values must survive a CSV/Parquet round-trip
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fmt", [RecordFormat.CSV, RecordFormat.PARQUET])
def test_nested_values_survive_tabular_round_trip(
    fmt: RecordFormat, tmp_path: Path
) -> None:
    """Nested values written as JSON text are decoded back into containers.

    CSV and Parquet cells are flat, so write_records JSON-encodes dicts and
    lists. Reading them back as strings would hand Argilla the wrong types on
    `dataset push`, breaking the documented download/push inverse pair.
    """
    original = [
        {
            "id": "r1",
            "status": "completed",
            "fields": {"text": "hello"},
            "labels": ["a", "b"],
            "note": "plain text",
        }
    ]
    target = tmp_path / f"records.{fmt.value}"

    write_records(original, target, fmt)
    restored = read_records(target, fmt)

    assert restored[0]["fields"] == {"text": "hello"}
    assert restored[0]["labels"] == ["a", "b"]
    assert restored[0]["note"] == "plain text"


def test_plain_text_is_not_misread_as_json(tmp_path: Path) -> None:
    """Ordinary prose is left alone by the decode step."""
    target = tmp_path / "records.csv"
    write_records(
        [{"text": "not {json} at all", "other": "3"}], target, RecordFormat.CSV
    )

    restored = read_records(target, RecordFormat.CSV)

    assert restored[0]["text"] == "not {json} at all"
    assert restored[0]["other"] == "3"


def test_download_csv_then_push_preserves_structure(
    runner: CliRunner, credentials: None, fake_client: FakeArgilla
) -> None:
    """The full `download --fmt csv` -> `push` workflow keeps nested fields."""
    result = runner.invoke(
        app, ["dataset", "download", "reviews", "-w", "nlp-lab", "--fmt", "csv"]
    )
    assert result.exit_code == 0, result.output

    with Path("reviews.csv").open(encoding="utf-8", newline="") as handle:
        on_disk = list(csv.DictReader(handle))
    assert on_disk[0]["fields"] == '{"text": "hello"}'  # flat on disk

    result = runner.invoke(
        app, ["dataset", "push", "intents", "-w", "nlp-lab", "--from", "reviews.csv"]
    )
    assert result.exit_code == 0, result.output

    intents = next(ds for ds in fake_client._datasets if ds.name == "intents")
    assert intents.records.logged[0]["fields"] == {"text": "hello"}


# ---------------------------------------------------------------------------
# Connectivity failures keep their type, and so their exit code
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status_code", "expected_exit"),
    [(401, 10), (403, 10), (503, 11)],
)
def test_doctor_maps_connectivity_failure_by_status(
    status_code: int,
    expected_exit: int,
    runner: CliRunner,
    credentials: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`config doctor` reports auth vs network, not a blanket exit 1.

    Stringifying the probe's exception discarded the status code, so every
    failure collapsed into a generic error.
    """
    _use_client(monkeypatch, _FailingClient(status_code))

    result = runner.invoke(app, ["config", "doctor"])

    assert result.exit_code == expected_exit, result.output


@pytest.mark.parametrize(
    ("status_code", "expected_exit"),
    [(401, 10), (503, 11)],
)
def test_server_health_maps_connectivity_failure_by_status(
    status_code: int,
    expected_exit: int,
    runner: CliRunner,
    credentials: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`server health` does not label a 401 as a network failure."""
    _use_client(monkeypatch, _FailingClient(status_code))

    result = runner.invoke(app, ["server", "health"])

    assert result.exit_code == expected_exit, result.output


# ---------------------------------------------------------------------------
# ARGILLA_CLI_PROFILE selects the profile that config set/get target
# ---------------------------------------------------------------------------


def test_config_set_honours_environment_profile(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`config set` writes to $ARGILLA_CLI_PROFILE, not the current profile.

    Otherwise credentials silently land in the wrong server's profile.
    """
    assert (
        runner.invoke(
            app,
            [
                "config",
                "set",
                "api_url",
                "https://prod.example.com",
                "--profile",
                "prod",
            ],
        ).exit_code
        == 0
    )
    assert runner.invoke(app, ["config", "use", "prod"]).exit_code == 0

    monkeypatch.setenv("ARGILLA_CLI_PROFILE", "staging")
    result = runner.invoke(
        app, ["config", "set", "api_url", "https://staging.example.com"]
    )
    assert result.exit_code == 0, result.output
    assert "staging" in result.output

    monkeypatch.delenv("ARGILLA_CLI_PROFILE")
    prod = runner.invoke(
        app, ["-o", "json", "config", "get", "api_url", "--profile", "prod"]
    )
    assert json.loads(prod.stdout)["value"] == "https://prod.example.com"


def test_config_get_honours_environment_profile(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`config get` reads from $ARGILLA_CLI_PROFILE too."""
    for name, url in (
        ("prod", "https://prod.example.com"),
        ("stg", "https://stg.example.com"),
    ):
        runner.invoke(app, ["config", "set", "api_url", url, "--profile", name])
    runner.invoke(app, ["config", "use", "prod"])

    monkeypatch.setenv("ARGILLA_CLI_PROFILE", "stg")
    result = runner.invoke(app, ["-o", "json", "config", "get", "api_url"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["value"] == "https://stg.example.com"


# ---------------------------------------------------------------------------
# --limit applies to the filtered stream
# ---------------------------------------------------------------------------


def test_limit_applies_after_completed_filter(
    runner: CliRunner, credentials: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--limit N --completed-only` yields N completed records, not zero.

    The limit used to cap the source stream, so a run of leading pending
    records could exhaust it before any completed record was seen.
    """
    workspace = FakeWorkspace("nlp-lab")
    records = [
        {"id": "p1", "status": "pending"},
        {"id": "p2", "status": "pending"},
        {"id": "c1", "status": "completed"},
        {"id": "c2", "status": "completed"},
    ]
    dataset = FakeDataset("mixed", "nlp-lab", records)
    _use_client(monkeypatch, FakeArgilla(workspaces=[workspace], datasets=[dataset]))

    result = runner.invoke(
        app,
        [
            "dataset",
            "download",
            "mixed",
            "-w",
            "nlp-lab",
            "--completed-only",
            "--limit",
            "1",
        ],
    )

    assert result.exit_code == 0, result.output
    lines = Path("mixed.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["id"] == "c1"


# ---------------------------------------------------------------------------
# Second review round
# ---------------------------------------------------------------------------


def test_push_mapping_preserves_containers(
    runner: CliRunner, credentials: None, fake_client: FakeArgilla
) -> None:
    """`push --map` builds structured records rather than flattening them.

    Reusing the export-oriented scalarization meant a mapping that produced a
    `fields` dict reached `records.log()` as a JSON string, which Argilla
    rejects.
    """
    Path("in.jsonl").write_text(
        json.dumps({"body": {"text": "hi"}, "tags": ["a", "b"]}) + "\n",
        encoding="utf-8",
    )
    Path("map.json").write_text(
        json.dumps({"fields": "body", "labels": "tags"}), encoding="utf-8"
    )

    result = runner.invoke(
        app,
        [
            "dataset",
            "push",
            "intents",
            "-w",
            "nlp-lab",
            "--from",
            "in.jsonl",
            "--map",
            "map.json",
        ],
    )

    assert result.exit_code == 0, result.output
    intents = next(ds for ds in fake_client._datasets if ds.name == "intents")
    logged = intents.records.logged[0]
    assert logged["fields"] == {"text": "hi"}
    assert logged["labels"] == ["a", "b"]


def test_push_can_still_opt_into_flattening(
    runner: CliRunner, credentials: None, fake_client: FakeArgilla
) -> None:
    """An explicit --list-policy on push still flattens, for flat schemas."""
    Path("in.jsonl").write_text(
        json.dumps({"tags": ["a", "b"]}) + "\n", encoding="utf-8"
    )
    Path("map.json").write_text(json.dumps({"labels": "tags"}), encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "dataset",
            "push",
            "intents",
            "-w",
            "nlp-lab",
            "--from",
            "in.jsonl",
            "--map",
            "map.json",
            "--list-policy",
            "join",
            "--list-sep",
            "|",
        ],
    )

    assert result.exit_code == 0, result.output
    intents = next(ds for ds in fake_client._datasets if ds.name == "intents")
    assert intents.records.logged[0]["labels"] == "a|b"


def test_download_mapping_still_flattens_by_default(
    runner: CliRunner, credentials: None
) -> None:
    """Export keeps its flattening default; only push changed."""
    Path("map.json").write_text(json.dumps({"text": "fields"}), encoding="utf-8")

    result = runner.invoke(
        app,
        ["dataset", "download", "reviews", "-w", "nlp-lab", "--map", "map.json"],
    )

    assert result.exit_code == 0, result.output
    first = json.loads(
        Path("reviews.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    assert isinstance(first["text"], str)


def test_hub_preflight_checks_jinja2(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Hub check fails when jinja2 is absent.

    argilla depends on `datasets` and `huggingface_hub` itself, so a check
    covering only those two passes in a base install and never fires. jinja2
    is the dependency the `hub` extra actually adds.
    """
    import builtins

    from argilla_cli.commands.dataset import _require_hub
    from argilla_cli.errors import MissingExtraError

    real_import = builtins.__import__

    def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "jinja2":
            raise ImportError("No module named 'jinja2'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(MissingExtraError) as excinfo:
        _require_hub()
    assert excinfo.value.exit_code == 13
    assert "argilla-cli[hub]" in str(excinfo.value)


def test_missing_parquet_engine_on_read_is_validation_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing Parquet engine on read exits 13 with install guidance.

    The write path already mapped this; the read path let a bare ImportError
    escape as a generic exit 1.
    """
    import pandas as pd

    from argilla_cli.errors import MissingExtraError

    target = tmp_path / "records.parquet"
    write_records([{"id": "r1"}], target, RecordFormat.PARQUET)

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise ImportError("Unable to find a usable engine")

    monkeypatch.setattr(pd, "read_parquet", boom)

    with pytest.raises(MissingExtraError) as excinfo:
        read_records(target, RecordFormat.PARQUET)
    assert excinfo.value.exit_code == 13
    assert "argilla-cli[export]" in str(excinfo.value)


# ---------------------------------------------------------------------------
# Third review round
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fmt", [RecordFormat.CSV, RecordFormat.PARQUET])
def test_absent_keys_are_not_restored_as_empty_cells(
    fmt: RecordFormat, tmp_path: Path
) -> None:
    """A key a record never had must not come back as "" or NaN.

    Tabular writers pad every row to the union of columns, so a record
    without `metadata` gained an empty cell. Restoring that as a value sent
    `metadata: ""` to Argilla, which is not a mapping and can get the whole
    log() batch rejected.
    """
    rows = [
        {"id": "r1", "fields": {"text": "a"}, "metadata": {"k": "v"}},
        {"id": "r2", "fields": {"text": "b"}},
    ]
    target = tmp_path / f"records.{fmt.value}"

    write_records(rows, target, fmt)
    restored = read_records(target, fmt)

    assert restored[0]["metadata"] == {"k": "v"}
    assert "metadata" not in restored[1]
    assert restored[1]["fields"] == {"text": "b"}


def test_push_of_heterogeneous_csv_omits_absent_keys(
    runner: CliRunner, credentials: None, fake_client: FakeArgilla, tmp_path: Path
) -> None:
    """End to end: pushing a ragged CSV does not invent empty properties."""
    source = Path("ragged.csv")
    write_records(
        [
            {"id": "r1", "metadata": {"k": "v"}},
            {"id": "r2"},
        ],
        source,
        RecordFormat.CSV,
    )

    result = runner.invoke(
        app, ["dataset", "push", "intents", "-w", "nlp-lab", "--from", str(source)]
    )

    assert result.exit_code == 0, result.output
    intents = next(ds for ds in fake_client._datasets if ds.name == "intents")
    assert "metadata" not in intents.records.logged[1]


@pytest.mark.parametrize(
    "url",
    ["http://?x=1", "https://host:99999", "http://", "not-a-url"],
)
def test_malformed_api_urls_are_validation_errors(url: str, runner: CliRunner) -> None:
    """A malformed API URL exits 13, not an unclassified 1 from the SDK.

    Making api_url optional dropped its AnyHttpUrl annotation; the
    replacement scheme-prefix check let an empty host or an out-of-range
    port through to client construction.
    """
    result = runner.invoke(app, ["--api-url", url, "--api-key", "k", "config", "show"])

    assert result.exit_code == 13, result.output
    assert "api_url" in result.output


def test_valid_api_url_is_untouched(runner: CliRunner) -> None:
    """A good URL still passes and is not rewritten."""
    result = runner.invoke(
        app,
        [
            "-o",
            "json",
            "--api-url",
            "https://argilla.example.com",
            "--api-key",
            "k",
            "config",
            "show",
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["api_url"] == "https://argilla.example.com"


def test_from_hub_forwards_the_configured_token(
    runner: CliRunner, credentials: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An HF token held in config reaches from-hub, as it already did to-hub."""
    import argilla

    monkeypatch.setenv("HF_TOKEN", "hf_secret_token_value")
    captured: dict[str, Any] = {}

    def fake_from_hub(repo_id: str, **kwargs: Any) -> FakeDataset:
        captured.update(kwargs)
        return FakeDataset("imported", "nlp-lab")

    monkeypatch.setattr(argilla.Dataset, "from_hub", staticmethod(fake_from_hub))

    result = runner.invoke(
        app, ["dataset", "from-hub", "org/ds", "--name", "imported", "-w", "nlp-lab"]
    )

    assert result.exit_code == 0, result.output
    assert captured["token"] == "hf_secret_token_value"


def test_from_hub_does_not_double_create(
    runner: CliRunner, credentials: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """from-hub must not call .create() on the dataset the SDK already made.

    Under settings="auto" the SDK persists the dataset itself (from_disk and
    the Settings branch both call create()). Calling it again here would hit
    a 409 on a dataset that imported fine.
    """
    import argilla

    created: list[str] = []

    class Tracking(FakeDataset):
        def create(self) -> Tracking:
            created.append(self.name)
            return self

    monkeypatch.setattr(
        argilla.Dataset,
        "from_hub",
        staticmethod(lambda repo_id, **kw: Tracking("imported", "nlp-lab")),
    )

    result = runner.invoke(app, ["dataset", "from-hub", "org/ds", "-w", "nlp-lab"])

    assert result.exit_code == 0, result.output
    assert created == []


def test_from_hub_reports_a_configuration_url_clearly(
    runner: CliRunner, credentials: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A str return (settings="ui") is reported, not rendered as a record."""
    import argilla

    monkeypatch.setattr(
        argilla.Dataset,
        "from_hub",
        staticmethod(lambda repo_id, **kw: "https://argilla.example.com/configure"),
    )

    result = runner.invoke(
        app, ["-o", "json", "dataset", "from-hub", "org/ds", "-w", "nlp-lab"]
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["configure_url"].startswith("https://")


# ---------------------------------------------------------------------------
# Fourth review round
# ---------------------------------------------------------------------------


def test_copy_does_not_carry_server_ids(
    runner: CliRunner, credentials: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Copied records must not reuse the source's server-assigned ids.

    records.log() is an upsert -- the SDK documents that a record carrying a
    known `id` is *updated*. Copying ids across would ask the server to touch
    the source dataset's records instead of creating independent ones.
    """
    import argilla

    workspace = FakeWorkspace("nlp-lab")
    source = FakeDataset(
        "src",
        "nlp-lab",
        [
            {"id": "r1", "_server_id": "s1", "fields": {"text": "a"}},
            {"id": "r2", "_server_id": "s2", "fields": {"text": "b"}},
        ],
    )
    _use_client(monkeypatch, FakeArgilla(workspaces=[workspace], datasets=[source]))

    created: list[FakeDataset] = []

    def factory(**kwargs: Any) -> Any:
        dataset = FakeDataset(kwargs.get("name", "copy"), "nlp-lab", [])

        class _Wrapper:
            def create(self) -> FakeDataset:
                created.append(dataset)
                return dataset

        return _Wrapper()

    monkeypatch.setattr(argilla, "Dataset", factory)

    result = runner.invoke(app, ["dataset", "copy", "src", "src-copy", "-w", "nlp-lab"])

    assert result.exit_code == 0, result.output
    logged = created[0].records.logged
    assert len(logged) == 2
    assert all("id" not in r and "_server_id" not in r for r in logged)
    assert {r["fields"]["text"] for r in logged} == {"a", "b"}


@pytest.mark.parametrize("bad_url", ["ftp://host", "https://:bad", "notaurl"])
def test_config_set_rejects_invalid_urls_before_writing(
    bad_url: str, runner: CliRunner
) -> None:
    """`config set api_url` validates up front instead of storing junk.

    Persisting an unusable value and reporting success left a profile that
    looks configured but cannot connect, with the error only surfacing later.
    """
    result = runner.invoke(app, ["config", "set", "api_url", bad_url])

    assert result.exit_code == 13, result.output

    listed = runner.invoke(app, ["-o", "json", "config", "list"])
    assert bad_url not in listed.stdout


def test_config_set_still_accepts_a_valid_url(runner: CliRunner) -> None:
    """The guard does not block legitimate values."""
    result = runner.invoke(
        app, ["config", "set", "api_url", "https://argilla.example.com"]
    )
    assert result.exit_code == 0, result.output

    got = runner.invoke(app, ["-o", "json", "config", "get", "api_url"])
    assert json.loads(got.stdout)["value"] == "https://argilla.example.com"


def test_unsupported_user_role_is_a_usage_error(runner: CliRunner) -> None:
    """An invalid --role fails at the CLI boundary, not as a generic error.

    The unconstrained string used to reach the SDK, whose pydantic failure
    mapped to exit 1. The enum makes it a usage error and lists the choices.
    """
    result = runner.invoke(
        app, ["user", "create", "jane", "--password", "pw", "--role", "superadmin"]
    )

    assert result.exit_code == 2, result.output


@pytest.mark.parametrize("role", ["annotator", "admin", "owner"])
def test_supported_user_roles_are_accepted(
    role: str, runner: CliRunner, credentials: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """All three roles Argilla defines still work, `owner` included."""
    import argilla

    captured: dict[str, Any] = {}

    def factory(**kwargs: Any) -> Any:
        captured.update(kwargs)

        class _Wrapper:
            def create(self) -> FakeUser:
                return FakeUser(kwargs["username"], role=kwargs["role"])

        return _Wrapper()

    monkeypatch.setattr(argilla, "User", factory)

    result = runner.invoke(
        app, ["user", "create", "jane", "--password", "pw", "--role", role]
    )

    assert result.exit_code == 0, result.output
    assert captured["role"] == role


# ---------------------------------------------------------------------------
# Fifth review round: exception types must survive their wrappers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status_code", "expected_exit"), [(401, 10), (403, 10), (503, 11)]
)
def test_record_fetch_failures_keep_their_exit_code(
    status_code: int,
    expected_exit: int,
    runner: CliRunner,
    credentials: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 401 while fetching records exits 10, not a validation error 13.

    Fetching records is a network call, so wrapping every failure as a
    validation error overwrote auth and transport failures with exit 13.
    """
    workspace = FakeWorkspace("nlp-lab")
    dataset = FakeDataset("boom", "nlp-lab", [])
    dataset.records = _FailingRecords(  # type: ignore[assignment]
        _ApiError("record page failed", status_code)
    )
    _use_client(monkeypatch, FakeArgilla(workspaces=[workspace], datasets=[dataset]))

    result = runner.invoke(app, ["dataset", "download", "boom", "-w", "nlp-lab"])

    assert result.exit_code == expected_exit, result.output


def test_unrecognised_record_failure_still_gets_context(
    runner: CliRunner, credentials: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unclassifiable failure keeps its explanatory wrapper."""
    workspace = FakeWorkspace("nlp-lab")
    dataset = FakeDataset("boom", "nlp-lab", [])
    dataset.records = _FailingRecords(  # type: ignore[assignment]
        TypeError("something structural")
    )
    _use_client(monkeypatch, FakeArgilla(workspaces=[workspace], datasets=[dataset]))

    result = runner.invoke(app, ["dataset", "download", "boom", "-w", "nlp-lab"])

    assert result.exit_code == 13, result.output
    assert "failed to read records" in result.output


@pytest.mark.parametrize(("status_code", "expected_exit"), [(401, 10), (503, 11)])
def test_server_info_preserves_probe_failures(
    status_code: int,
    expected_exit: int,
    runner: CliRunner,
    credentials: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`server info` reports a 401 as auth, not a blanket network error."""

    class Http:
        def get(self, path: str) -> Any:
            raise _ApiError(f"{path} failed", status_code)

    client = FakeArgilla()
    client.http_client = Http()  # type: ignore[attr-defined]
    _use_client(monkeypatch, client)

    result = runner.invoke(app, ["server", "info"])

    assert result.exit_code == expected_exit, result.output


def test_server_info_without_transport_is_a_network_error(
    runner: CliRunner, credentials: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no HTTP transport at all there is no cause to preserve."""
    _use_client(monkeypatch, FakeArgilla())

    result = runner.invoke(app, ["server", "info"])

    assert result.exit_code == 11, result.output


def test_malformed_settings_file_is_a_validation_error(
    runner: CliRunner, credentials: None
) -> None:
    """`dataset create --settings <malformed>` exits 13, not a generic 1.

    The file is user input, so a JSON parse failure belongs to the
    validation bucket rather than falling through as unclassified.
    """
    Path("broken.json").write_text("{not json", encoding="utf-8")

    result = runner.invoke(
        app,
        ["dataset", "create", "newds", "-w", "nlp-lab", "--settings", "broken.json"],
    )

    assert result.exit_code == 13, result.output
    assert "Traceback" not in result.output


# ---------------------------------------------------------------------------
# Sixth review round
# ---------------------------------------------------------------------------


def test_native_parquet_list_columns_are_readable(tmp_path: Path) -> None:
    """A Parquet file with a native list column can be read back.

    pandas+pyarrow hands such a cell back as a NumPy array, and the
    missing-value check compared it with `==`, whose array result raises
    "truth value of an array is ambiguous" -- so valid records never
    reached records.log().
    """
    import pandas as pd

    target = tmp_path / "native.parquet"
    pd.DataFrame(
        [{"id": "r1", "labels": ["a", "b"]}, {"id": "r2", "labels": ["c"]}]
    ).to_parquet(target, index=False)

    restored = read_records(target, RecordFormat.PARQUET)

    assert len(restored) == 2
    assert list(restored[0]["labels"]) == ["a", "b"]


def test_push_of_native_parquet_reaches_the_server(
    runner: CliRunner, credentials: None, fake_client: FakeArgilla
) -> None:
    """End to end: such a file pushes instead of erroring out."""
    import pandas as pd

    pd.DataFrame([{"id": "r1", "labels": ["a", "b"]}]).to_parquet(
        "native.parquet", index=False
    )

    result = runner.invoke(
        app, ["dataset", "push", "intents", "-w", "nlp-lab", "--from", "native.parquet"]
    )

    assert result.exit_code == 0, result.output
    intents = next(ds for ds in fake_client._datasets if ds.name == "intents")
    assert len(intents.records.logged) == 1


class _FailingRecords:
    """Records whose iteration fails, as a mid-fetch network error would."""

    def __init__(self, error: Exception) -> None:
        self.error = error

    def __iter__(self) -> Iterator[dict[str, Any]]:
        raise self.error

    def to_list(self, flatten: bool = False) -> list[dict[str, Any]]:
        raise self.error


class _StreamingRecords:
    """Records that page lazily, and count how many were actually fetched.

    Shaped like the SDK's accessor: streaming is reached through ``__iter__``,
    not a zero-argument ``__call__``. An earlier version of this double
    defined ``__call__`` and so would have passed even if the production code
    depended on a call signature the real SDK does not offer.
    """

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.fetched = 0

    def __iter__(self) -> Iterator[dict[str, Any]]:
        for row in self.rows:
            self.fetched += 1
            yield row

    def to_list(self, flatten: bool = False) -> list[dict[str, Any]]:
        self.fetched = len(self.rows)
        return list(self.rows)


def test_limit_stops_fetching_early(
    runner: CliRunner, credentials: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--limit N` must stop pulling records, not slice after fetching all.

    to_list() materialises the whole dataset first, so a limit of 10 against
    a million records still transferred a million.
    """
    workspace = FakeWorkspace("nlp-lab")
    dataset = FakeDataset("big", "nlp-lab", [])
    streaming = _StreamingRecords(
        [{"id": f"r{i}", "status": "completed"} for i in range(1000)]
    )
    dataset.records = streaming  # type: ignore[assignment]
    _use_client(monkeypatch, FakeArgilla(workspaces=[workspace], datasets=[dataset]))

    result = runner.invoke(
        app, ["dataset", "download", "big", "-w", "nlp-lab", "--limit", "5"]
    )

    assert result.exit_code == 0, result.output
    assert len(Path("big.jsonl").read_text(encoding="utf-8").strip().splitlines()) == 5
    # The whole point: only a handful of records were ever pulled.
    assert streaming.fetched < 50, (
        f"fetched {streaming.fetched} records for a limit of 5"
    )


def test_limit_with_filter_still_fills_the_quota(
    runner: CliRunner, credentials: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Streaming keeps pulling until N *matching* records are found."""
    workspace = FakeWorkspace("nlp-lab")
    dataset = FakeDataset("mixed", "nlp-lab", [])
    rows: list[dict[str, Any]] = [
        {"id": f"p{i}", "status": "pending"} for i in range(20)
    ]
    rows += [{"id": f"c{i}", "status": "completed"} for i in range(5)]
    dataset.records = _StreamingRecords(rows)  # type: ignore[assignment]
    _use_client(monkeypatch, FakeArgilla(workspaces=[workspace], datasets=[dataset]))

    result = runner.invoke(
        app,
        [
            "dataset",
            "download",
            "mixed",
            "-w",
            "nlp-lab",
            "--completed-only",
            "--limit",
            "3",
        ],
    )

    assert result.exit_code == 0, result.output
    lines = Path("mixed.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3
    assert json.loads(lines[0])["id"] == "c0"


def test_copy_rolls_back_when_logging_records_fails(
    runner: CliRunner, credentials: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed record copy must not leave the destination behind.

    Otherwise the command errors out but the half-made dataset lingers, and
    retrying with the same name collides with it.
    """
    import argilla

    workspace = FakeWorkspace("nlp-lab")
    source = FakeDataset("src", "nlp-lab", [{"id": "r1", "fields": {"text": "a"}}])
    _use_client(monkeypatch, FakeArgilla(workspaces=[workspace], datasets=[source]))

    created: list[FakeDataset] = []

    def factory(**kwargs: Any) -> Any:
        dataset = FakeDataset(kwargs.get("name", "copy"), "nlp-lab", [])

        def boom(records: Any) -> None:
            raise _ApiError("bulk upsert rejected", 422)

        dataset.records.log = boom  # type: ignore[assignment]

        class _Wrapper:
            def create(self) -> FakeDataset:
                created.append(dataset)
                return dataset

        return _Wrapper()

    monkeypatch.setattr(argilla, "Dataset", factory)

    result = runner.invoke(app, ["dataset", "copy", "src", "src-copy", "-w", "nlp-lab"])

    assert result.exit_code == 13, result.output
    assert len(created) == 1
    assert created[0].deleted is True, "destination should have been rolled back"


def test_copy_does_not_create_when_the_source_read_fails(
    runner: CliRunner, credentials: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reading the source happens first, so a fetch failure creates nothing."""
    import argilla

    workspace = FakeWorkspace("nlp-lab")
    source = FakeDataset("src", "nlp-lab", [])
    source.records = _FailingRecords(  # type: ignore[assignment]
        _ApiError("source unreadable", 503)
    )
    _use_client(monkeypatch, FakeArgilla(workspaces=[workspace], datasets=[source]))

    created: list[Any] = []

    def factory(**kwargs: Any) -> Any:
        class _Wrapper:
            def create(self) -> FakeDataset:
                created.append(kwargs)
                return FakeDataset("src-copy", "nlp-lab", [])

        return _Wrapper()

    monkeypatch.setattr(argilla, "Dataset", factory)

    result = runner.invoke(app, ["dataset", "copy", "src", "src-copy", "-w", "nlp-lab"])

    assert result.exit_code == 11, result.output
    assert created == [], "nothing should have been created"


# ---------------------------------------------------------------------------
# Eighth review round
# ---------------------------------------------------------------------------


def test_flatten_with_limit_also_stops_fetching_early(
    runner: CliRunner, credentials: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--flatten --limit N` must stream too, not materialise everything.

    The flatten path went through eager to_list(), so this documented
    combination kept the full-download failure mode after streaming landed.
    """
    workspace = FakeWorkspace("nlp-lab")
    dataset = FakeDataset("big", "nlp-lab", [])
    streaming = _StreamingRecords(
        [{"id": f"r{i}", "fields": {"text": f"t{i}"}} for i in range(1000)]
    )
    dataset.records = streaming  # type: ignore[assignment]
    _use_client(monkeypatch, FakeArgilla(workspaces=[workspace], datasets=[dataset]))

    result = runner.invoke(
        app,
        ["dataset", "download", "big", "-w", "nlp-lab", "--flatten", "--limit", "5"],
    )

    assert result.exit_code == 0, result.output
    lines = Path("big.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 5
    assert "fields.text" in json.loads(lines[0])
    assert streaming.fetched < 50, f"fetched {streaming.fetched} for a limit of 5"


def test_failed_download_does_not_destroy_the_existing_file(
    runner: CliRunner, credentials: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A download that fails part-way leaves the previous export intact.

    Writing straight to the destination truncated it on open, so a failure
    mid-stream destroyed a good file and left a partial one behind.
    """
    workspace = FakeWorkspace("nlp-lab")
    dataset = FakeDataset("boom", "nlp-lab", [])

    class _HalfBroken:
        def __iter__(self) -> Iterator[dict[str, Any]]:
            yield {"id": "r1"}
            raise _ApiError("connection dropped", 503)

    dataset.records = _HalfBroken()  # type: ignore[assignment]
    _use_client(monkeypatch, FakeArgilla(workspaces=[workspace], datasets=[dataset]))

    target = Path("boom.jsonl")
    target.write_text("PREVIOUS GOOD EXPORT\n", encoding="utf-8")

    result = runner.invoke(
        app, ["dataset", "download", "boom", "-w", "nlp-lab", "--force"]
    )

    assert result.exit_code == 11, result.output
    assert target.read_text(encoding="utf-8") == "PREVIOUS GOOD EXPORT\n"
    leftovers = [p.name for p in Path().iterdir() if p.name.endswith(".partial")]
    assert leftovers == [], f"temporary files left behind: {leftovers}"


def test_successful_download_replaces_the_target(
    runner: CliRunner, credentials: None
) -> None:
    """The atomic write still produces the file on the happy path."""
    Path("reviews.jsonl").write_text("stale\n", encoding="utf-8")

    result = runner.invoke(
        app, ["dataset", "download", "reviews", "-w", "nlp-lab", "--force"]
    )

    assert result.exit_code == 0, result.output
    lines = Path("reviews.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert "stale" not in lines[0]


def test_pydantic_validation_errors_are_bad_input() -> None:
    """A model rejecting user data exits 13, not an unclassified 1."""
    import pydantic

    from argilla_cli.errors import ValidationError as CLIValidationError

    class Model(pydantic.BaseModel):
        count: int

    try:
        Model(count="not a number")  # type: ignore[arg-type]
    except pydantic.ValidationError as exc:
        mapped = map_exception(exc)
        assert isinstance(mapped, CLIValidationError)
        assert mapped.exit_code == 13
    else:  # pragma: no cover - the model must reject this
        raise AssertionError("expected a validation error")


@pytest.mark.parametrize(
    "error_name",
    [
        "RecordsIngestionError",
        "SettingsError",
        "MetadataError",
        "ArgillaSerializeError",
    ],
)
def test_argilla_local_errors_are_bad_input(error_name: str) -> None:
    """Argilla's local validation errors are input problems, not network ones.

    These are raised while serialising or ingesting what the user supplied.
    Reporting `RecordsIngestionError` as exit 11 told people to check their
    connection when their records were malformed.
    """
    import argilla._exceptions as exceptions

    from argilla_cli.errors import ValidationError as CLIValidationError

    mapped = map_exception(getattr(exceptions, error_name)("bad input"))

    assert isinstance(mapped, CLIValidationError), f"{error_name} -> {type(mapped)}"
    assert mapped.exit_code == 13


# ---------------------------------------------------------------------------
# Ninth review round
# ---------------------------------------------------------------------------


def test_push_limit_stops_parsing_the_input(
    runner: CliRunner, credentials: None, fake_client: FakeArgilla
) -> None:
    """`push --limit N` must not parse the whole file first.

    Proven by putting invalid JSON *after* the Nth record: if parsing stops
    at the limit the upload succeeds, and if it does not the malformed line
    fails an upload that should never have read it.
    """
    Path("big.jsonl").write_text(
        "\n".join(json.dumps({"id": f"r{i}"}) for i in range(3))
        + "\n{ this line is not valid json\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "dataset",
            "push",
            "intents",
            "-w",
            "nlp-lab",
            "--from",
            "big.jsonl",
            "--limit",
            "2",
        ],
    )

    assert result.exit_code == 0, result.output
    intents = next(ds for ds in fake_client._datasets if ds.name == "intents")
    assert len(intents.records.logged) == 2


def test_push_without_limit_still_reports_malformed_input(
    runner: CliRunner, credentials: None
) -> None:
    """Without a limit the whole file is read, so bad lines are still caught."""
    Path("bad.jsonl").write_text(
        json.dumps({"id": "r1"}) + "\n{ not json\n", encoding="utf-8"
    )

    result = runner.invoke(
        app, ["dataset", "push", "intents", "-w", "nlp-lab", "--from", "bad.jsonl"]
    )

    assert result.exit_code == 13, result.output


def test_csv_push_limit_stops_parsing(
    runner: CliRunner, credentials: None, fake_client: FakeArgilla
) -> None:
    """The CSV reader honours the limit too."""
    write_records(
        [{"id": f"r{i}"} for i in range(50)], Path("many.csv"), RecordFormat.CSV
    )

    result = runner.invoke(
        app,
        [
            "dataset",
            "push",
            "intents",
            "-w",
            "nlp-lab",
            "--from",
            "many.csv",
            "--limit",
            "3",
        ],
    )

    assert result.exit_code == 0, result.output
    intents = next(ds for ds in fake_client._datasets if ds.name == "intents")
    assert len(intents.records.logged) == 3


def test_corrupt_parquet_is_a_validation_error(
    runner: CliRunner, credentials: None
) -> None:
    """A corrupt or mislabelled Parquet file exits 13, not a generic 1.

    The engine raises its own decoding error (pyarrow.lib.ArrowInvalid),
    which the mapper has no reason to recognise.
    """
    Path("corrupt.parquet").write_text("definitely not parquet", encoding="utf-8")

    result = runner.invoke(
        app,
        ["dataset", "push", "intents", "-w", "nlp-lab", "--from", "corrupt.parquet"],
    )

    assert result.exit_code == 13, result.output
    assert "Traceback" not in result.output


@pytest.mark.parametrize(
    ("status_code", "expected_exit"),
    [
        (400, 13),
        (401, 10),
        (403, 10),
        (404, 12),
        (408, 11),
        (409, 13),
        (413, 13),
        (422, 13),
        (429, 11),
        (500, 11),
        (503, 11),
    ],
)
def test_every_http_status_maps_to_a_documented_code(
    status_code: int, expected_exit: int
) -> None:
    """No HTTP status falls through to the unclassified exit 1.

    408, 413 and 429 previously did: a rate-limited request was reported as
    an unexpected error rather than as something to retry.
    """
    mapped = map_exception(_ApiError(f"http {status_code}", status_code))

    assert mapped.exit_code == expected_exit, f"{status_code} -> {mapped.exit_code}"


# ---------------------------------------------------------------------------
# Tenth review round
# ---------------------------------------------------------------------------


def test_copy_streams_records_in_batches(
    runner: CliRunner, credentials: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`copy --with-records` must not hold the whole source in memory.

    Verified by observing the *sizes* of the log() calls: a single call
    carrying every record means the source was materialised, whatever the
    final count says.
    """
    import argilla

    from argilla_cli.commands.dataset import _COPY_BATCH_SIZE

    total = _COPY_BATCH_SIZE * 2 + 7
    workspace = FakeWorkspace("nlp-lab")
    source = FakeDataset("src", "nlp-lab", [])
    source.records = _StreamingRecords(  # type: ignore[assignment]
        [{"id": f"r{i}"} for i in range(total)]
    )
    _use_client(monkeypatch, FakeArgilla(workspaces=[workspace], datasets=[source]))

    batch_sizes: list[int] = []
    created: list[FakeDataset] = []

    def factory(**kwargs: Any) -> Any:
        dataset = FakeDataset(kwargs.get("name", "copy"), "nlp-lab", [])
        original_log = dataset.records.log

        def counting_log(records: list[dict[str, Any]]) -> None:
            batch_sizes.append(len(records))
            original_log(records)

        dataset.records.log = counting_log  # type: ignore[assignment]

        class _Wrapper:
            def create(self) -> FakeDataset:
                created.append(dataset)
                return dataset

        return _Wrapper()

    monkeypatch.setattr(argilla, "Dataset", factory)

    result = runner.invoke(app, ["dataset", "copy", "src", "src-copy", "-w", "nlp-lab"])

    assert result.exit_code == 0, result.output
    assert sum(batch_sizes) == total, "every record should still be copied"
    assert len(batch_sizes) == 3, f"expected batched uploads, got {batch_sizes}"
    assert max(batch_sizes) <= _COPY_BATCH_SIZE
    assert len(created[0].records.logged) == total


def test_copy_reads_the_first_batch_before_creating(
    runner: CliRunner, credentials: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unreadable source still creates nothing, despite the streaming.

    This is the property from the earlier rollback fix, and batching must not
    cost it: the first batch is read before the destination exists.
    """
    import argilla

    workspace = FakeWorkspace("nlp-lab")
    source = FakeDataset("src", "nlp-lab", [])
    source.records = _FailingRecords(  # type: ignore[assignment]
        _ApiError("source unreadable", 503)
    )
    _use_client(monkeypatch, FakeArgilla(workspaces=[workspace], datasets=[source]))

    created: list[Any] = []

    def factory(**kwargs: Any) -> Any:
        class _Wrapper:
            def create(self) -> FakeDataset:
                created.append(kwargs)
                return FakeDataset("src-copy", "nlp-lab", [])

        return _Wrapper()

    monkeypatch.setattr(argilla, "Dataset", factory)

    result = runner.invoke(app, ["dataset", "copy", "src", "src-copy", "-w", "nlp-lab"])

    assert result.exit_code == 11, result.output
    assert created == [], "nothing should have been created"


def test_copy_rolls_back_when_a_later_batch_fails(
    runner: CliRunner, credentials: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failure after the first batch still removes the destination.

    Batching widens the window in which the destination exists but is
    incomplete, so the rollback has to cover a mid-copy failure too.
    """
    import argilla

    from argilla_cli.commands.dataset import _COPY_BATCH_SIZE

    workspace = FakeWorkspace("nlp-lab")
    source = FakeDataset("src", "nlp-lab", [])
    source.records = _StreamingRecords(  # type: ignore[assignment]
        [{"id": f"r{i}"} for i in range(_COPY_BATCH_SIZE * 2)]
    )
    _use_client(monkeypatch, FakeArgilla(workspaces=[workspace], datasets=[source]))

    created: list[FakeDataset] = []

    def factory(**kwargs: Any) -> Any:
        dataset = FakeDataset(kwargs.get("name", "copy"), "nlp-lab", [])
        calls = {"n": 0}

        def failing_log(records: list[dict[str, Any]]) -> None:
            calls["n"] += 1
            if calls["n"] > 1:
                raise _ApiError("bulk upsert rejected mid-copy", 422)

        dataset.records.log = failing_log  # type: ignore[assignment]

        class _Wrapper:
            def create(self) -> FakeDataset:
                created.append(dataset)
                return dataset

        return _Wrapper()

    monkeypatch.setattr(argilla, "Dataset", factory)

    result = runner.invoke(app, ["dataset", "copy", "src", "src-copy", "-w", "nlp-lab"])

    assert result.exit_code == 13, result.output
    assert created[0].deleted is True, "destination should have been rolled back"


# ---------------------------------------------------------------------------
# Eleventh review round
# ---------------------------------------------------------------------------


def test_copy_rolls_back_on_keyboard_interrupt(
    runner: CliRunner, credentials: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ctrl+C during a copy still removes the half-made destination.

    KeyboardInterrupt derives from BaseException, so an `except Exception`
    rollback lets it past -- and a long copy is exactly when someone reaches
    for Ctrl+C.
    """
    import argilla

    from argilla_cli.commands.dataset import _COPY_BATCH_SIZE

    workspace = FakeWorkspace("nlp-lab")
    source = FakeDataset("src", "nlp-lab", [])
    source.records = _StreamingRecords(  # type: ignore[assignment]
        [{"id": f"r{i}"} for i in range(_COPY_BATCH_SIZE * 2)]
    )
    _use_client(monkeypatch, FakeArgilla(workspaces=[workspace], datasets=[source]))

    created: list[FakeDataset] = []

    def factory(**kwargs: Any) -> Any:
        dataset = FakeDataset(kwargs.get("name", "copy"), "nlp-lab", [])
        calls = {"n": 0}

        def interrupted_log(records: list[dict[str, Any]]) -> None:
            calls["n"] += 1
            if calls["n"] > 1:
                raise KeyboardInterrupt()

        dataset.records.log = interrupted_log  # type: ignore[assignment]

        class _Wrapper:
            def create(self) -> FakeDataset:
                created.append(dataset)
                return dataset

        return _Wrapper()

    monkeypatch.setattr(argilla, "Dataset", factory)

    runner.invoke(app, ["dataset", "copy", "src", "src-copy", "-w", "nlp-lab"])

    assert created and created[0].deleted is True, (
        "destination should be rolled back even on interrupt"
    )


# --------------------------------------------------------------------------
# Round twelve
# --------------------------------------------------------------------------


def test_csv_export_does_not_retain_every_record(tmp_path: Path) -> None:
    """CSV must stream, not collect the dataset to learn its columns.

    CSV needs the union of every row's keys before it can write a header, and
    the obvious way to get that -- `list(rows)` -- quietly undid the lazy
    record iterator on the format most likely to be pointed at a large
    export. The rows paged in a batch at a time and then all sat in memory.

    Retention is measured from inside the producer, because after the write
    returns every row is unreachable either way. Each row is dropped by the
    generator right after it is yielded, so anything still alive is held by
    the writer.
    """
    import gc
    import weakref

    class _Row(dict[str, Any]):
        """A dict that can be weak-referenced, so retention is observable."""

    alive_at_end: list[int] = []
    row_count = 50

    def produce() -> Iterator[dict[str, Any]]:
        refs: list[weakref.ReferenceType[_Row]] = []
        for index in range(row_count):
            row = _Row(id=f"r{index}", text="x" * 200)
            refs.append(weakref.ref(row))
            yield row
            del row
        gc.collect()
        alive_at_end.append(sum(1 for ref in refs if ref() is not None))

    written = write_records(produce(), tmp_path / "out.csv", RecordFormat.CSV)

    assert written == row_count
    # The writer's own loop variable still holds the final row; everything
    # earlier must have been released. Materialising held all 50.
    assert alive_at_end and alive_at_end[0] < 10, (
        f"writer retained {alive_at_end} of {row_count} rows; CSV is not streaming"
    )


def test_csv_export_keeps_the_union_of_columns_while_streaming(
    tmp_path: Path,
) -> None:
    """Streaming must not narrow the header to the first row's keys.

    Spooling makes it tempting to fix the header early. A row whose extra
    keys arrive later would then be silently truncated, because the writer
    uses `extrasaction="ignore"`.
    """
    rows = [
        {"id": "r0", "text": "first"},
        {"id": "r1", "metadata": {"split": "train"}},
        {"id": "r2", "score": 0.5},
    ]
    path = tmp_path / "ragged.csv"
    write_records(iter(rows), path, RecordFormat.CSV)

    with path.open(newline="") as handle:
        parsed = list(csv.DictReader(handle))

    assert set(parsed[0]) == {"id", "text", "metadata", "score"}
    assert [r["id"] for r in parsed] == ["r0", "r1", "r2"]
    # Nested values survive the spool's JSON round-trip as encoded text...
    assert json.loads(parsed[1]["metadata"]) == {"split": "train"}
    # ...and absent keys stay absent rather than becoming empty strings.
    assert read_records(path, RecordFormat.CSV)[0] == {"id": "r0", "text": "first"}


def test_saving_a_profile_leaves_the_old_config_intact_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed config write must not destroy the stored credentials.

    `write_bytes` truncates the only copy of every API key the moment it
    opens the file, so a full disk or a Ctrl+C left invalid TOML behind --
    which `load_store` then rejects, failing every later command too.
    """
    import argilla_cli.atomic_io as atomic_io
    from argilla_cli.profiles import ProfileStore, load_store, save_store

    config = tmp_path / "config.toml"
    monkeypatch.setenv("ARGILLA_CLI_CONFIG", str(config))

    save_store(
        ProfileStore(
            current="prod",
            profiles={"prod": {"api_key": "keep-me"}},
            path=config,
        )
    )
    original = config.read_bytes()

    def boom(src: Any, dst: Any) -> None:
        raise OSError("No space left on device")

    monkeypatch.setattr(atomic_io.os, "replace", boom)

    with pytest.raises(OSError):
        save_store(
            ProfileStore(
                current="staging",
                profiles={"staging": {"api_key": "new"}},
                path=config,
            )
        )

    assert config.read_bytes() == original
    assert load_store().profiles["prod"]["api_key"] == "keep-me"
    # ...and the abandoned temporary file is not left behind next to it.
    assert [p.name for p in tmp_path.glob("*.partial")] == []


def test_saved_config_is_never_briefly_world_readable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The credentials are private *before* the file becomes the config.

    Asserting the mode of the finished file proves nothing: writing in place
    and calling `chmod` afterwards ends up at 0o600 too. What changed is the
    window in between, where the old code had the API keys sitting in a
    world-readable file. So the check is made against the file actually
    holding the payload, at the moment it is put into place.
    """
    import argilla_cli.atomic_io as atomic_io
    from argilla_cli.profiles import ProfileStore, save_store

    real_replace = atomic_io.os.replace
    modes: list[int] = []

    def record_then_replace(src: Any, dst: Any) -> None:
        modes.append(Path(src).stat().st_mode & 0o777)
        real_replace(src, dst)

    monkeypatch.setattr(atomic_io.os, "replace", record_then_replace)

    config = tmp_path / "config.toml"
    save_store(ProfileStore(profiles={"prod": {"api_key": "secret"}}, path=config))

    assert modes, "config was written in place rather than replaced atomically"
    assert modes[0] & 0o077 == 0, (
        f"credentials were group/world readable ({modes[0]:o})"
    )
    assert config.stat().st_mode & 0o077 == 0


# --------------------------------------------------------------------------
# Round thirteen
# --------------------------------------------------------------------------


def test_csv_limit_does_not_parse_the_row_beyond_it(tmp_path: Path) -> None:
    """`--limit N` must not parse row N+1, only stop after it.

    `for row in reader:` pulls the next row and *then* runs the loop body's
    check, so the row past the limit was parsed before the break. A row the
    caller excluded could therefore still fail the read -- here an oversized
    field, which `csv` refuses outright.
    """
    limit = csv.field_size_limit()
    csv.field_size_limit(2000)
    try:
        path = tmp_path / "oversized.csv"
        path.write_text("id,text\nr1,ok\nr2," + "x" * 5000 + "\n", encoding="utf-8")

        assert read_records(path, RecordFormat.CSV, limit=1) == [
            {"id": "r1", "text": "ok"}
        ]
        # Without a limit the same row is reached, and still fails.
        with pytest.raises(csv.Error):
            read_records(path, RecordFormat.CSV)
    finally:
        csv.field_size_limit(limit)


def test_jsonl_limit_does_not_decode_the_line_beyond_it(tmp_path: Path) -> None:
    """The same guarantee for JSONL, which needed more than a loop change.

    A text-mode handle decodes a whole buffer per read, so an undecodable
    byte on line 2 raised while line 1 was being fetched -- checking the
    count before pulling could not help, because the damage happens inside
    the read. Reading bytes and decoding per line is what actually bounds it.
    """
    path = tmp_path / "bad-bytes.jsonl"
    path.write_bytes(b'{"id": "r1"}\n{"id": "\xff\xfe"}\n')

    assert read_records(path, RecordFormat.JSONL, limit=1) == [{"id": "r1"}]


def test_undecodable_input_is_a_validation_error_with_its_line(
    tmp_path: Path,
) -> None:
    """A bad byte names its line and exits 13, rather than escaping as 1.

    `UnicodeDecodeError` is not something `map_exception` recognises, so it
    previously became the unclassified exit code with no indication of where
    in the file the problem was.
    """
    from argilla_cli.errors import ValidationError

    path = tmp_path / "bad-bytes.jsonl"
    path.write_bytes(b'{"id": "r1"}\n{"id": "\xff\xfe"}\n')

    with pytest.raises(ValidationError) as excinfo:
        read_records(path, RecordFormat.JSONL)

    assert excinfo.value.exit_code == 13
    assert ":2:" in str(excinfo.value)


def test_blank_lines_do_not_consume_the_limit(tmp_path: Path) -> None:
    """Bounding the read must not turn blank lines into records.

    This is why the JSONL path cannot simply be `islice` over the handle:
    that stops after N *lines*, so a file padded with blanks would return
    fewer records than asked for.
    """
    path = tmp_path / "padded.jsonl"
    path.write_text('{"id": 1}\n\n\n{"id": 2}\n{"id": 3}\n', encoding="utf-8")

    assert read_records(path, RecordFormat.JSONL, limit=2) == [{"id": 1}, {"id": 2}]


def test_push_uploads_in_bounded_batches(
    runner: CliRunner, credentials: None, fake_client: FakeArgilla, monkeypatch: Any
) -> None:
    """An unlimited push must not hold the whole file in memory.

    `read_records` returned a list and `--map` built a second one beside it,
    so a large upload could exhaust memory before a single record reached the
    server. Reading, mapping and logging now run a batch at a time.

    Observed through the size of each `log()` call: bounded batches mean no
    single call carries more than the batch size.
    """
    from argilla_cli.commands import dataset as dataset_cmd

    monkeypatch.setattr(dataset_cmd, "_PUSH_BATCH_SIZE", 10)

    Path("many.jsonl").write_text(
        "\n".join(json.dumps({"id": f"r{i}"}) for i in range(25)) + "\n",
        encoding="utf-8",
    )

    intents = next(ds for ds in fake_client._datasets if ds.name == "intents")
    batches: list[int] = []
    real_log = intents.records.log

    def counting_log(records: list[dict[str, Any]]) -> None:
        batches.append(len(records))
        real_log(records)

    intents.records.log = counting_log  # type: ignore[assignment]

    result = runner.invoke(
        app, ["dataset", "push", "intents", "-w", "nlp-lab", "--from", "many.jsonl"]
    )

    assert result.exit_code == 0, result.output
    assert batches == [10, 10, 5], f"expected bounded batches, got {batches}"
    assert len(intents.records.logged) == 25
    assert "25" in result.output


def test_push_still_reports_an_empty_file_without_calling_the_server(
    runner: CliRunner, credentials: None, fake_client: FakeArgilla
) -> None:
    """Batching must not lose the empty-input check, or make it cost a call.

    The first batch is read before anything is logged, so an empty file is
    still exit 13 and the server is never touched.
    """
    Path("empty.jsonl").write_text("", encoding="utf-8")

    intents = next(ds for ds in fake_client._datasets if ds.name == "intents")

    def fail_log(records: list[dict[str, Any]]) -> None:
        raise AssertionError("log() called for an empty input")

    intents.records.log = fail_log  # type: ignore[assignment]

    result = runner.invoke(
        app, ["dataset", "push", "intents", "-w", "nlp-lab", "--from", "empty.jsonl"]
    )

    assert result.exit_code == 13, result.output
