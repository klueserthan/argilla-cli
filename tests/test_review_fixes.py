"""Regressions for the issues raised in review on PR #1.

Each test pins a behaviour that was wrong when the PR was first opened.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from argilla_cli.main import app
from argilla_cli.records_io import RecordFormat, read_records, write_records

from .conftest import FakeArgilla, FakeDataset, FakeWorkspace


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
