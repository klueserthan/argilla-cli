"""Unit tests for ``argilla_cli.records_io``.

These exercise the format inference, JMESPath mapping, and read/write helpers
directly -- no CLI runner involved, since none of this depends on Typer.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from argilla_cli.errors import ValidationError
from argilla_cli.records_io import (
    ListPolicy,
    RecordFormat,
    compile_mapping,
    filter_completed,
    infer_format,
    iter_dataset_records,
    load_mapping,
    read_records,
    resolve_target_path,
    transform_record,
    write_records,
)

from .conftest import FakeRecords


class _StubDataset:
    """Minimal stand-in exposing only a ``.records`` attribute."""

    def __init__(self, records: FakeRecords) -> None:
        self.records = records


# ---------------------------------------------------------------------------
# infer_format
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("suffix", "expected"),
    [
        (".jsonl", RecordFormat.JSONL),
        (".ndjson", RecordFormat.JSONL),
        (".csv", RecordFormat.CSV),
        (".parquet", RecordFormat.PARQUET),
    ],
)
def test_infer_format_from_suffix(suffix: str, expected: RecordFormat) -> None:
    """The format is inferred from the file suffix when not overridden."""
    assert infer_format(Path(f"data{suffix}"), None) is expected


def test_infer_format_explicit_override_wins() -> None:
    """An explicit format takes precedence over the file suffix."""
    result = infer_format(Path("data.csv"), RecordFormat.PARQUET)
    assert result is RecordFormat.PARQUET


def test_infer_format_unknown_suffix_raises() -> None:
    """An unrecognised suffix without an explicit format is a validation error."""
    with pytest.raises(ValidationError):
        infer_format(Path("data.xyz"), None)


# ---------------------------------------------------------------------------
# load_mapping
# ---------------------------------------------------------------------------


def test_load_mapping_valid_file(tmp_path: Path) -> None:
    """A well-formed mapping file is returned as a plain dict."""
    path = tmp_path / "mapping.json"
    path.write_text(json.dumps({"text": "fields.text"}), encoding="utf-8")
    assert load_mapping(path) == {"text": "fields.text"}


def test_load_mapping_missing_file_raises(tmp_path: Path) -> None:
    """A missing mapping file is a validation error."""
    with pytest.raises(ValidationError):
        load_mapping(tmp_path / "missing.json")


def test_load_mapping_non_json_suffix_raises(tmp_path: Path) -> None:
    """Only ``.json`` mapping files are accepted."""
    path = tmp_path / "mapping.yaml"
    path.write_text("text: fields.text", encoding="utf-8")
    with pytest.raises(ValidationError):
        load_mapping(path)


def test_load_mapping_malformed_json_raises(tmp_path: Path) -> None:
    """Invalid JSON content is a validation error."""
    path = tmp_path / "mapping.json"
    path.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(ValidationError):
        load_mapping(path)


def test_load_mapping_json_array_raises(tmp_path: Path) -> None:
    """A JSON array instead of an object is a validation error."""
    path = tmp_path / "mapping.json"
    path.write_text(json.dumps(["text", "fields.text"]), encoding="utf-8")
    with pytest.raises(ValidationError):
        load_mapping(path)


def test_load_mapping_non_string_value_raises(tmp_path: Path) -> None:
    """Every mapping value must be a JMESPath string."""
    path = tmp_path / "mapping.json"
    path.write_text(json.dumps({"text": 1}), encoding="utf-8")
    with pytest.raises(ValidationError):
        load_mapping(path)


# ---------------------------------------------------------------------------
# compile_mapping
# ---------------------------------------------------------------------------


def test_compile_mapping_invalid_expression_raises() -> None:
    """An invalid JMESPath expression is reported as a validation error."""
    with pytest.raises(ValidationError):
        compile_mapping({"text": "["})


# ---------------------------------------------------------------------------
# transform_record
# ---------------------------------------------------------------------------


def test_transform_record_join_uses_custom_separator() -> None:
    """JOIN policy joins list results with the caller-supplied separator."""
    compiled = compile_mapping({"combo": "[status, id]"})
    record = {"id": "r1", "status": "completed"}
    result = transform_record(record, compiled, ListPolicy.JOIN, "|")
    assert result == {"combo": "completed|r1"}


def test_transform_record_first_takes_first_element() -> None:
    """FIRST policy keeps only the first element of a list result."""
    compiled = compile_mapping({"combo": "[status, id]"})
    record = {"id": "r1", "status": "completed"}
    result = transform_record(record, compiled, ListPolicy.FIRST)
    assert result == {"combo": "completed"}


def test_transform_record_error_policy_raises() -> None:
    """ERROR policy refuses a list result instead of silently coercing it."""
    compiled = compile_mapping({"combo": "[status, id]"})
    record = {"id": "r1", "status": "completed"}
    with pytest.raises(ValidationError):
        transform_record(record, compiled, ListPolicy.ERROR)


def test_transform_record_dict_value_is_json_encoded() -> None:
    """A dict-valued mapping result is JSON-encoded to a scalar string."""
    compiled = compile_mapping({"meta": "fields"})
    record: dict[str, Any] = {"fields": {"a": 1, "b": 2}}
    result = transform_record(record, compiled)
    assert result == {"meta": json.dumps({"a": 1, "b": 2}, ensure_ascii=False)}


# ---------------------------------------------------------------------------
# write_records / read_records round trips
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fmt", [RecordFormat.JSONL, RecordFormat.CSV, RecordFormat.PARQUET]
)
def test_write_then_read_round_trips(tmp_path: Path, fmt: RecordFormat) -> None:
    """Writing then reading back a format returns the same records."""
    rows = [{"id": "r1", "text": "hello"}, {"id": "r2", "text": "world"}]
    path = tmp_path / f"out.{fmt.value}"

    count = write_records(rows, path, fmt)

    assert count == 2
    read_back = read_records(path, fmt)
    assert [dict(row) for row in read_back] == rows


def test_write_records_jsonl_produces_one_json_object_per_line(
    tmp_path: Path,
) -> None:
    """Each JSONL line is independently valid JSON."""
    rows = [{"id": "r1"}, {"id": "r2"}]
    path = tmp_path / "out.jsonl"
    write_records(rows, path, RecordFormat.JSONL)

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert [json.loads(line) for line in lines] == rows


def test_read_records_malformed_jsonl_mentions_line_number(tmp_path: Path) -> None:
    """A malformed JSONL line raises a validation error naming its line."""
    path = tmp_path / "bad.jsonl"
    path.write_text('{"ok": true}\nnot json\n', encoding="utf-8")
    with pytest.raises(ValidationError, match="2"):
        read_records(path, RecordFormat.JSONL)


def test_read_records_bare_array_line_is_rejected(tmp_path: Path) -> None:
    """A JSONL line holding a JSON array (not object) is a validation error."""
    path = tmp_path / "bad.jsonl"
    path.write_text("[1, 2, 3]\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        read_records(path, RecordFormat.JSONL)


# ---------------------------------------------------------------------------
# resolve_target_path
# ---------------------------------------------------------------------------


def test_resolve_target_path_defaults_to_base_dir(tmp_path: Path) -> None:
    """With no explicit output, the target is ``base_dir/stem.fmt``."""
    result = resolve_target_path(None, "reviews", RecordFormat.JSONL, tmp_path)
    assert result == tmp_path / "reviews.jsonl"


def test_resolve_target_path_existing_directory(tmp_path: Path) -> None:
    """An existing directory target becomes ``dir/stem.fmt``."""
    target_dir = tmp_path / "out"
    target_dir.mkdir()
    result = resolve_target_path(target_dir, "reviews", RecordFormat.CSV, tmp_path)
    assert result == target_dir / "reviews.csv"


def test_resolve_target_path_bare_stem_gets_suffix(tmp_path: Path) -> None:
    """A bare stem with no suffix gets the format's suffix appended."""
    result = resolve_target_path(Path("out"), "reviews", RecordFormat.JSONL, tmp_path)
    assert result == Path("out.jsonl")


def test_resolve_target_path_explicit_suffix_is_unchanged(tmp_path: Path) -> None:
    """An explicit path that already has a suffix is returned unchanged."""
    explicit = Path("custom.jsonl")
    result = resolve_target_path(explicit, "reviews", RecordFormat.CSV, tmp_path)
    assert result == explicit


# ---------------------------------------------------------------------------
# filter_completed / iter_dataset_records
# ---------------------------------------------------------------------------


def test_filter_completed_keeps_only_completed_when_enabled() -> None:
    """--completed-only drops every row whose status isn't 'completed'."""
    rows = [{"status": "completed"}, {"status": "pending"}]
    result = list(filter_completed(rows, True))
    assert result == [{"status": "completed"}]


def test_filter_completed_passthrough_when_disabled() -> None:
    """Without the flag, every row passes through untouched."""
    rows = [{"status": "completed"}, {"status": "pending"}]
    result = list(filter_completed(rows, False))
    assert result == rows


def test_iter_dataset_records_yields_plain_dicts() -> None:
    """Records come back as plain dicts, matching the fake SDK's rows."""
    rows = [{"id": "r1", "fields": {"text": "hello"}}]
    dataset = _StubDataset(FakeRecords(rows))
    assert list(iter_dataset_records(dataset)) == rows


def test_iter_dataset_records_respects_limit() -> None:
    """``limit`` truncates the yielded records."""
    rows = [{"id": f"r{i}"} for i in range(5)]
    dataset = _StubDataset(FakeRecords(rows))
    assert len(list(iter_dataset_records(dataset, limit=2))) == 2


def test_iter_dataset_records_flatten_uses_dotted_keys() -> None:
    """``flatten=True`` turns nested dicts into dotted-key scalars."""
    rows = [{"id": "r1", "fields": {"text": "hello"}}]
    dataset = _StubDataset(FakeRecords(rows))
    result = list(iter_dataset_records(dataset, flatten=True))
    assert result == [{"id": "r1", "fields.text": "hello"}]


def test_iter_dataset_records_without_records_attribute_raises() -> None:
    """An object with no ``.records`` attribute is a validation error."""
    with pytest.raises(ValidationError):
        list(iter_dataset_records(object()))
