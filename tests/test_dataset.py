"""CLI tests for ``argilla_cli.commands.dataset``.

Covers every subcommand in ``dataset.py`` against ``FakeArgilla``. Global
flags (``-o``, ``-y``, ``-w``) come before the subcommand name, matching the
root callback's option surface.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from argilla_cli.main import app

from .conftest import FakeArgilla, FakeDataset


def _dataset_factory(created: list[FakeDataset]) -> type:
    """Build a stand-in for ``argilla.Dataset(...).create()``.

    Records every constructed dataset in ``created`` so tests can inspect
    what was logged onto it afterwards.
    """

    class _Factory:
        def __init__(self, **kwargs: Any) -> None:
            self.dataset = FakeDataset(kwargs["name"], kwargs["workspace"])
            created.append(self.dataset)

        def create(self) -> FakeDataset:
            return self.dataset

    return _Factory


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


def test_list_table_shows_all_datasets(runner: CliRunner, credentials: None) -> None:
    """`dataset list` renders all three datasets across both workspaces."""
    result = runner.invoke(app, ["dataset", "list"])

    assert result.exit_code == 0, result.output
    assert "reviews" in result.output
    assert "intents" in result.output
    assert "nlp-lab" in result.output
    assert "archive" in result.output


def test_list_json_rows_carry_name_workspace_id(
    runner: CliRunner, credentials: None
) -> None:
    """`-o json dataset list` rows expose name/workspace/id."""
    result = runner.invoke(app, ["-o", "json", "dataset", "list"])

    assert result.exit_code == 0, result.output
    rows = json.loads(result.stdout)
    assert len(rows) == 3
    for row in rows:
        assert {"name", "workspace", "id"} <= row.keys()


def test_list_filtered_by_workspace(runner: CliRunner, credentials: None) -> None:
    """`dataset list -w nlp-lab` returns only that workspace's two datasets."""
    result = runner.invoke(app, ["-o", "json", "dataset", "list", "-w", "nlp-lab"])

    assert result.exit_code == 0, result.output
    rows = json.loads(result.stdout)
    assert len(rows) == 2
    assert {row["workspace"] for row in rows} == {"nlp-lab"}


def test_list_missing_workspace_is_not_found(
    runner: CliRunner, credentials: None
) -> None:
    """`dataset list -w ghost` exits 12."""
    result = runner.invoke(app, ["dataset", "list", "-w", "ghost"])
    assert result.exit_code == 12, result.output


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------


def test_show_ambiguous_name_without_workspace_exits_13(
    runner: CliRunner, credentials: None
) -> None:
    """`dataset show reviews` without -w hits the ambiguity path."""
    result = runner.invoke(app, ["dataset", "show", "reviews"])

    assert result.exit_code == 13, result.output
    assert "nlp-lab" in result.output
    assert "archive" in result.output


def test_show_with_workspace_succeeds(runner: CliRunner, credentials: None) -> None:
    """`dataset show reviews -w nlp-lab` renders fields and questions."""
    result = runner.invoke(
        app, ["-o", "json", "dataset", "show", "reviews", "-w", "nlp-lab"]
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["fields"] == ["text"]
    assert payload["questions"] == ["label"]


def test_show_missing_dataset_in_workspace_exits_12(
    runner: CliRunner, credentials: None
) -> None:
    """`dataset show ghost -w nlp-lab` exits 12."""
    result = runner.invoke(app, ["dataset", "show", "ghost", "-w", "nlp-lab"])
    assert result.exit_code == 12, result.output


# ---------------------------------------------------------------------------
# progress
# ---------------------------------------------------------------------------


def test_progress_shows_totals(runner: CliRunner, credentials: None) -> None:
    """`dataset progress` shows total/completed/pending counts."""
    result = runner.invoke(
        app, ["-o", "json", "dataset", "progress", "reviews", "-w", "nlp-lab"]
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload == {"total": 2, "completed": 1, "pending": 1}


def test_progress_by_user_renders_per_user_rows(
    runner: CliRunner, credentials: None
) -> None:
    """`--by-user` breaks progress down per annotator."""
    result = runner.invoke(
        app,
        [
            "-o",
            "json",
            "dataset",
            "progress",
            "reviews",
            "-w",
            "nlp-lab",
            "--by-user",
        ],
    )

    assert result.exit_code == 0, result.output
    rows = json.loads(result.stdout)
    assert any(row["user"] == "alice" for row in rows)


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


def test_delete_with_yes_flag_succeeds(
    runner: CliRunner, credentials: None, fake_client: FakeArgilla
) -> None:
    """`-y dataset delete intents -w nlp-lab` deletes without prompting."""
    result = runner.invoke(app, ["-y", "dataset", "delete", "intents", "-w", "nlp-lab"])

    assert result.exit_code == 0, result.output
    target = fake_client.datasets("intents", workspace="nlp-lab")
    assert target.deleted is True


def test_delete_aborted_when_not_confirmed(
    runner: CliRunner, credentials: None, fake_client: FakeArgilla
) -> None:
    """Declining the confirmation prompt aborts and leaves the dataset intact."""
    result = runner.invoke(
        app, ["dataset", "delete", "intents", "-w", "nlp-lab"], input="n\n"
    )

    assert result.exit_code != 0
    target = fake_client.datasets("intents", workspace="nlp-lab")
    assert target.deleted is False


# ---------------------------------------------------------------------------
# settings
# ---------------------------------------------------------------------------


def test_settings_renders_serialized_settings(
    runner: CliRunner, credentials: None
) -> None:
    """`dataset settings` renders the dataset's serialized settings."""
    result = runner.invoke(
        app, ["-o", "json", "dataset", "settings", "reviews", "-w", "nlp-lab"]
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["guidelines"] == "Annotate carefully."
    assert payload["fields"] == [{"name": "text"}]


def test_settings_export_writes_valid_json_file(
    runner: CliRunner, credentials: None
) -> None:
    """`--export out.json` writes a real, JSON-parseable file."""
    export_path = Path("out.json")
    result = runner.invoke(
        app,
        [
            "dataset",
            "settings",
            "reviews",
            "-w",
            "nlp-lab",
            "--export",
            str(export_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert export_path.exists()
    payload = json.loads(export_path.read_text(encoding="utf-8"))
    assert payload["guidelines"] == "Annotate carefully."


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------


def test_create_from_exported_settings_file(
    runner: CliRunner, credentials: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`dataset create` builds a dataset from a `settings --export` file."""
    import argilla

    settings_path = Path("settings.json")
    export_result = runner.invoke(
        app,
        [
            "dataset",
            "settings",
            "reviews",
            "-w",
            "nlp-lab",
            "--export",
            str(settings_path),
        ],
    )
    assert export_result.exit_code == 0, export_result.output

    monkeypatch.setattr(argilla.Settings, "from_json", lambda path: object())
    created: list[FakeDataset] = []
    monkeypatch.setattr(argilla, "Dataset", _dataset_factory(created))

    result = runner.invoke(
        app,
        [
            "dataset",
            "create",
            "newds",
            "-w",
            "nlp-lab",
            "--settings",
            str(settings_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert len(created) == 1
    assert created[0].name == "newds"


def test_create_without_workspace_exits_13(
    runner: CliRunner, credentials: None
) -> None:
    """`dataset create` with no workspace anywhere is a validation error."""
    result = runner.invoke(
        app, ["dataset", "create", "newds", "--settings", "settings.json"]
    )
    assert result.exit_code == 13, result.output


# ---------------------------------------------------------------------------
# download
# ---------------------------------------------------------------------------


def test_download_writes_valid_jsonl(runner: CliRunner, credentials: None) -> None:
    """`dataset download` writes 2 lines of valid JSON."""
    result = runner.invoke(app, ["dataset", "download", "reviews", "-w", "nlp-lab"])

    assert result.exit_code == 0, result.output
    lines = Path("reviews.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    for line in lines:
        json.loads(line)


@pytest.mark.parametrize("fmt", ["jsonl", "csv"])
def test_download_completed_only_filters_identically_per_format(
    runner: CliRunner, credentials: None, fmt: str
) -> None:
    """--completed-only keeps exactly the 1 completed record, jsonl and csv alike."""
    result = runner.invoke(
        app,
        [
            "dataset",
            "download",
            "reviews",
            "-w",
            "nlp-lab",
            "--fmt",
            fmt,
            "--completed-only",
        ],
    )

    assert result.exit_code == 0, result.output
    if fmt == "jsonl":
        lines = Path("reviews.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        assert json.loads(lines[0])["id"] == "r1"
    else:
        with Path("reviews.csv").open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        assert len(rows) == 1
        assert rows[0]["id"] == "r1"


def test_download_csv_is_readable_without_pandas(
    runner: CliRunner, credentials: None
) -> None:
    """CSV export produces a header and rows, using only the csv module."""
    result = runner.invoke(
        app, ["dataset", "download", "reviews", "-w", "nlp-lab", "--fmt", "csv"]
    )

    assert result.exit_code == 0, result.output
    with Path("reviews.csv").open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
    assert reader.fieldnames is not None
    assert "id" in reader.fieldnames
    assert len(rows) == 2


def test_download_limit_truncates_records(runner: CliRunner, credentials: None) -> None:
    """--limit 1 writes only 1 record."""
    result = runner.invoke(
        app, ["dataset", "download", "reviews", "-w", "nlp-lab", "--limit", "1"]
    )

    assert result.exit_code == 0, result.output
    lines = Path("reviews.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1


def test_download_flatten_produces_dotted_key(
    runner: CliRunner, credentials: None
) -> None:
    """--flatten turns nested fields into a dotted 'fields.text' key."""
    result = runner.invoke(
        app, ["dataset", "download", "reviews", "-w", "nlp-lab", "--flatten"]
    )

    assert result.exit_code == 0, result.output
    first = json.loads(
        Path("reviews.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    assert "fields.text" in first
    assert "fields" not in first


def test_download_without_flatten_keeps_nested_fields(
    runner: CliRunner, credentials: None
) -> None:
    """Without --flatten, the 'fields' object stays nested."""
    result = runner.invoke(app, ["dataset", "download", "reviews", "-w", "nlp-lab"])

    assert result.exit_code == 0, result.output
    first = json.loads(
        Path("reviews.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    assert isinstance(first["fields"], dict)


def test_download_output_path_existing_directory(
    runner: CliRunner, credentials: None
) -> None:
    """-O somedir/ (an existing directory) places reviews.jsonl inside it."""
    Path("somedir").mkdir()
    result = runner.invoke(
        app, ["dataset", "download", "reviews", "-w", "nlp-lab", "-O", "somedir/"]
    )

    assert result.exit_code == 0, result.output
    assert Path("somedir/reviews.jsonl").exists()


def test_download_output_path_bare_stem_gets_jsonl_suffix(
    runner: CliRunner, credentials: None
) -> None:
    """-O out (no suffix) becomes out.jsonl."""
    result = runner.invoke(
        app, ["dataset", "download", "reviews", "-w", "nlp-lab", "-O", "out"]
    )

    assert result.exit_code == 0, result.output
    assert Path("out.jsonl").exists()


def test_download_refuses_to_overwrite_existing_file(
    runner: CliRunner, credentials: None
) -> None:
    """Downloading over an existing file exits 13 without touching it."""
    target = Path("reviews.jsonl")
    target.write_text("existing", encoding="utf-8")

    result = runner.invoke(app, ["dataset", "download", "reviews", "-w", "nlp-lab"])

    assert result.exit_code == 13, result.output
    assert target.read_text(encoding="utf-8") == "existing"


def test_download_force_overwrites_existing_file(
    runner: CliRunner, credentials: None
) -> None:
    """--force allows overwriting an existing output file."""
    target = Path("reviews.jsonl")
    target.write_text("existing", encoding="utf-8")

    result = runner.invoke(
        app, ["dataset", "download", "reviews", "-w", "nlp-lab", "--force"]
    )

    assert result.exit_code == 0, result.output
    assert target.read_text(encoding="utf-8") != "existing"


def test_download_map_produces_exact_requested_keys(
    runner: CliRunner, credentials: None
) -> None:
    """--map rows have exactly the mapped keys, with the mapped values."""
    mapping_path = Path("mapping.json")
    mapping_path.write_text(
        json.dumps({"text": "fields.text", "state": "status"}), encoding="utf-8"
    )

    result = runner.invoke(
        app,
        [
            "dataset",
            "download",
            "reviews",
            "-w",
            "nlp-lab",
            "--map",
            str(mapping_path),
        ],
    )

    assert result.exit_code == 0, result.output
    rows = [
        json.loads(line)
        for line in Path("reviews.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert rows == [
        {"text": "hello", "state": "completed"},
        {"text": "world", "state": "pending"},
    ]


def test_download_map_list_result_with_error_policy_exits_13(
    runner: CliRunner, credentials: None
) -> None:
    """A mapping expression yielding a list, under --list-policy error, fails."""
    mapping_path = Path("mapping.json")
    mapping_path.write_text(json.dumps({"combo": "[status, id]"}), encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "dataset",
            "download",
            "reviews",
            "-w",
            "nlp-lab",
            "--map",
            str(mapping_path),
            "--list-policy",
            "error",
        ],
    )

    assert result.exit_code == 13, result.output


def test_download_map_list_result_with_first_policy_succeeds(
    runner: CliRunner, credentials: None
) -> None:
    """--list-policy first keeps only the first element of a list result."""
    mapping_path = Path("mapping.json")
    mapping_path.write_text(json.dumps({"combo": "[status, id]"}), encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "dataset",
            "download",
            "reviews",
            "-w",
            "nlp-lab",
            "--map",
            str(mapping_path),
            "--list-policy",
            "first",
        ],
    )

    assert result.exit_code == 0, result.output
    rows = [
        json.loads(line)
        for line in Path("reviews.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert rows[0]["combo"] == "completed"


def test_download_map_list_result_with_join_policy_uses_separator(
    runner: CliRunner, credentials: None
) -> None:
    """--list-policy join --list-sep '|' joins the list with that separator."""
    mapping_path = Path("mapping.json")
    mapping_path.write_text(json.dumps({"combo": "[status, id]"}), encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "dataset",
            "download",
            "reviews",
            "-w",
            "nlp-lab",
            "--map",
            str(mapping_path),
            "--list-policy",
            "join",
            "--list-sep",
            "|",
        ],
    )

    assert result.exit_code == 0, result.output
    rows = [
        json.loads(line)
        for line in Path("reviews.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert rows[0]["combo"] == "completed|r1"


# ---------------------------------------------------------------------------
# push
# ---------------------------------------------------------------------------


def test_push_jsonl_logs_records_onto_the_target_dataset(
    runner: CliRunner, credentials: None, fake_client: FakeArgilla
) -> None:
    """Pushed jsonl records are logged onto the fake dataset."""
    source = Path("records.jsonl")
    source.write_text(
        '{"id": "x1", "text": "a"}\n{"id": "x2", "text": "b"}\n', encoding="utf-8"
    )

    result = runner.invoke(
        app, ["dataset", "push", "intents", "-w", "nlp-lab", "--from", str(source)]
    )

    assert result.exit_code == 0, result.output
    target = fake_client.datasets("intents", workspace="nlp-lab")
    assert len(target.records.logged) == 2
    assert {r["id"] for r in target.records.logged} == {"x1", "x2"}


def test_push_csv_infers_format_from_suffix(
    runner: CliRunner, credentials: None, fake_client: FakeArgilla
) -> None:
    """A .csv source is pushed without an explicit --fmt."""
    source = Path("records.csv")
    with source.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["id", "text"])
        writer.writerow(["x1", "a"])

    result = runner.invoke(
        app, ["dataset", "push", "intents", "-w", "nlp-lab", "--from", str(source)]
    )

    assert result.exit_code == 0, result.output
    target = fake_client.datasets("intents", workspace="nlp-lab")
    assert len(target.records.logged) == 1


def test_push_unknown_suffix_without_fmt_exits_13(
    runner: CliRunner, credentials: None
) -> None:
    """An unrecognised suffix with no --fmt override is a validation error."""
    source = Path("records.dat")
    source.write_text("irrelevant", encoding="utf-8")

    result = runner.invoke(
        app, ["dataset", "push", "intents", "-w", "nlp-lab", "--from", str(source)]
    )

    assert result.exit_code == 13, result.output


def test_push_missing_file_exits_13(runner: CliRunner, credentials: None) -> None:
    """Pushing a file that doesn't exist is a validation error."""
    result = runner.invoke(
        app,
        [
            "dataset",
            "push",
            "intents",
            "-w",
            "nlp-lab",
            "--from",
            "does-not-exist.jsonl",
        ],
    )
    assert result.exit_code == 13, result.output


def test_push_empty_file_exits_13(runner: CliRunner, credentials: None) -> None:
    """Pushing a file with no records is a validation error."""
    source = Path("empty.jsonl")
    source.write_text("", encoding="utf-8")

    result = runner.invoke(
        app, ["dataset", "push", "intents", "-w", "nlp-lab", "--from", str(source)]
    )
    assert result.exit_code == 13, result.output


# ---------------------------------------------------------------------------
# copy
# ---------------------------------------------------------------------------


def test_copy_with_records_logs_them_onto_the_new_dataset(
    runner: CliRunner, credentials: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`dataset copy` logs the source's 2 records onto the new dataset."""
    import argilla

    created: list[FakeDataset] = []
    monkeypatch.setattr(argilla, "Dataset", _dataset_factory(created))

    result = runner.invoke(
        app, ["dataset", "copy", "reviews", "reviews-copy", "-w", "nlp-lab"]
    )

    assert result.exit_code == 0, result.output
    assert len(created) == 1
    assert len(created[0].records.logged) == 2
    assert {r["id"] for r in created[0].records.logged} == {"r1", "r2"}


def test_copy_no_records_flag_copies_nothing(
    runner: CliRunner, credentials: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--no-records` creates the dataset without copying any records."""
    import argilla

    created: list[FakeDataset] = []
    monkeypatch.setattr(argilla, "Dataset", _dataset_factory(created))

    result = runner.invoke(
        app,
        [
            "dataset",
            "copy",
            "reviews",
            "reviews-copy",
            "-w",
            "nlp-lab",
            "--no-records",
        ],
    )

    assert result.exit_code == 0, result.output
    assert len(created) == 1
    assert created[0].records.logged == []


# ---------------------------------------------------------------------------
# to-hub / from-hub
#
# ``datasets`` and ``huggingface_hub`` are both importable in this
# environment (verified with `uv run python -c "import datasets"` and
# `... "import huggingface_hub"`), so ``_require_hub`` succeeds and these
# tests monkeypatch the Hub calls rather than asserting MissingExtraError.
# ---------------------------------------------------------------------------


def test_to_hub_calls_dataset_to_hub_with_expected_kwargs(
    runner: CliRunner,
    credentials: None,
    monkeypatch: pytest.MonkeyPatch,
    fake_client: FakeArgilla,
) -> None:
    """`dataset to-hub` delegates to the dataset's own `to_hub` method."""
    calls: list[tuple[str, dict[str, Any]]] = []
    target = fake_client.datasets("reviews", workspace="nlp-lab")

    def _fake_to_hub(repo_id: str, **kwargs: Any) -> None:
        calls.append((repo_id, kwargs))

    monkeypatch.setattr(target, "to_hub", _fake_to_hub, raising=False)

    result = runner.invoke(
        app, ["dataset", "to-hub", "reviews", "user/repo", "-w", "nlp-lab"]
    )

    assert result.exit_code == 0, result.output
    assert len(calls) == 1
    repo_id, kwargs = calls[0]
    assert repo_id == "user/repo"
    assert kwargs["with_records"] is True
    assert kwargs["private"] is False


def test_from_hub_creates_dataset_via_argilla_dataset_from_hub(
    runner: CliRunner, credentials: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`dataset from-hub` creates a dataset via `argilla.Dataset.from_hub`."""
    import argilla

    calls: list[tuple[str, dict[str, Any]]] = []

    def _fake_from_hub(repo_id: str, **kwargs: Any) -> FakeDataset:
        calls.append((repo_id, kwargs))
        return FakeDataset(kwargs["name"], kwargs["workspace"])

    monkeypatch.setattr(argilla.Dataset, "from_hub", _fake_from_hub)

    result = runner.invoke(
        app,
        [
            "-o",
            "json",
            "dataset",
            "from-hub",
            "user/repo",
            "--name",
            "imported",
            "-w",
            "nlp-lab",
        ],
    )

    assert result.exit_code == 0, result.output
    assert len(calls) == 1
    repo_id, kwargs = calls[0]
    assert repo_id == "user/repo"
    assert kwargs["name"] == "imported"
    assert kwargs["workspace"] == "nlp-lab"
    payload = json.loads(result.stdout)
    assert payload["name"] == "imported"
