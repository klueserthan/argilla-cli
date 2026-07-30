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
