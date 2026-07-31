"""Unit tests for ``argilla_cli.io_utils`` rendering and presentation state.

Each test drives ``configure``/``render`` directly and inspects stdout via
``capsys`` -- no CLI runner needed since none of this depends on Typer.
"""

from __future__ import annotations

import csv
import json

import pytest
import yaml

from argilla_cli import io_utils
from argilla_cli.io_utils import OutputFormat, print_error, print_ok, render


def test_json_list_of_dicts_renders_as_array(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A list of mappings renders as a JSON array."""
    io_utils.configure(output=OutputFormat.JSON)
    render([{"name": "a"}, {"name": "b"}])

    payload = json.loads(capsys.readouterr().out)

    assert payload == [{"name": "a"}, {"name": "b"}]
    io_utils.reset()


def test_json_single_dict_renders_as_object(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A single mapping renders as a bare JSON object, not a 1-element array.

    This matters for ``... -o json | jq .name``.
    """
    io_utils.configure(output=OutputFormat.JSON)
    render({"name": "reviews", "id": "1"})

    payload = json.loads(capsys.readouterr().out)

    assert payload == {"name": "reviews", "id": "1"}
    io_utils.reset()


def test_yaml_output_round_trips_via_safe_load(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """YAML output parses back to the same structure with ``yaml.safe_load``."""
    io_utils.configure(output=OutputFormat.YAML)
    render({"name": "reviews", "count": 2, "tags": ["a", "b"]})

    payload = yaml.safe_load(capsys.readouterr().out)

    assert payload == {"name": "reviews", "count": 2, "tags": ["a", "b"]}
    io_utils.reset()


def test_csv_header_respects_explicit_column_order(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The CSV header follows the given ``columns`` order exactly."""
    io_utils.configure(output=OutputFormat.CSV)
    render([{"a": "1", "b": "2", "c": "3"}], columns=["c", "a", "b"])

    header = capsys.readouterr().out.splitlines()[0]

    assert header == "c,a,b"
    io_utils.reset()


def test_table_empty_list_prints_default_message(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An empty list under table output prints the default empty message."""
    io_utils.configure(output=OutputFormat.TABLE)
    render([])

    assert "(no results)" in capsys.readouterr().out
    io_utils.reset()


def test_table_empty_list_honors_custom_empty_message(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A caller-supplied ``empty_message`` overrides the default text."""
    io_utils.configure(output=OutputFormat.TABLE)
    render([], empty_message="nothing to show")

    out = capsys.readouterr().out

    assert "nothing to show" in out
    assert "(no results)" not in out
    io_utils.reset()


def test_print_ok_suppressed_under_json_output(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """print_ok is silent when the output format is machine-readable."""
    io_utils.configure(output=OutputFormat.JSON)
    print_ok("done")

    assert capsys.readouterr().out == ""
    io_utils.reset()


def test_print_ok_suppressed_under_quiet(capsys: pytest.CaptureFixture[str]) -> None:
    """print_ok is silent under --quiet, even for table output."""
    io_utils.configure(output=OutputFormat.TABLE, quiet=True)
    print_ok("done")

    assert capsys.readouterr().out == ""
    io_utils.reset()


def test_print_ok_prints_under_plain_table_output(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """print_ok is visible for the default, unstructured table output."""
    io_utils.configure(output=OutputFormat.TABLE, quiet=False)
    print_ok("done")

    assert "done" in capsys.readouterr().out
    io_utils.reset()


def test_print_error_is_never_suppressed(capsys: pytest.CaptureFixture[str]) -> None:
    """print_error always writes, even under --quiet and JSON output."""
    io_utils.configure(output=OutputFormat.JSON, quiet=True)
    print_error("boom")

    assert "boom" in capsys.readouterr().err
    io_utils.reset()


def test_stringify_none_renders_as_empty_csv_field(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """None values render as an empty string, not the text 'None'."""
    io_utils.configure(output=OutputFormat.CSV)
    render([{"x": None}], columns=["x"])

    lines = capsys.readouterr().out.splitlines()
    rows = list(csv.reader(lines))

    assert rows == [["x"], [""]]
    io_utils.reset()


def test_stringify_bool_true_renders_lowercase(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Booleans render as lowercase 'true'/'false', not Python's 'True'."""
    io_utils.configure(output=OutputFormat.CSV)
    render([{"x": True}], columns=["x"])

    lines = capsys.readouterr().out.splitlines()
    rows = list(csv.reader(lines))

    assert rows == [["x"], ["true"]]
    io_utils.reset()


def test_stringify_nested_value_renders_as_json_text(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A nested list/dict value renders as its JSON text representation."""
    io_utils.configure(output=OutputFormat.CSV)
    render([{"x": [1, 2]}], columns=["x"])

    lines = capsys.readouterr().out.splitlines()
    rows = list(csv.reader(lines))

    assert rows[1] == [json.dumps([1, 2], ensure_ascii=False)]
    io_utils.reset()
