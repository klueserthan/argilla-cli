"""Output rendering and presentation state for the CLI.

This module owns *how* the CLI talks to the terminal: the selected output
format, verbosity, and the single ``render`` entry point that every command
uses. Keeping presentation state here (rather than in ``context``) avoids an
import cycle, since ``errors`` needs to print but ``context`` needs to raise.
"""

from __future__ import annotations

import csv
import json
import sys
from collections.abc import Iterable, Mapping, Sequence
from enum import StrEnum
from typing import Any

import yaml
from rich.console import Console
from rich.table import Table


class OutputFormat(StrEnum):
    """Supported rendering formats for structured command output."""

    TABLE = "table"
    JSON = "json"
    YAML = "yaml"
    CSV = "csv"


_console = Console(stderr=False)
_err_console = Console(stderr=True)

_output_format: OutputFormat = OutputFormat.TABLE
_verbose: bool = False
_quiet: bool = False


def configure(
    *,
    output: OutputFormat | None = None,
    verbose: bool | None = None,
    quiet: bool | None = None,
) -> None:
    """Set global presentation state. Called once from the root callback."""
    global _output_format, _verbose, _quiet
    if output is not None:
        _output_format = output
    if verbose is not None:
        _verbose = verbose
    if quiet is not None:
        _quiet = quiet


def reset() -> None:
    """Restore defaults. Used by tests to keep invocations independent."""
    configure(output=OutputFormat.TABLE, verbose=False, quiet=False)


def is_verbose() -> bool:
    return _verbose


def is_structured() -> bool:
    """True when output is machine-readable, so status chatter must be muted."""
    return _output_format is not OutputFormat.TABLE


def emit_json(obj: Any) -> None:
    json.dump(obj, sys.stdout, ensure_ascii=False, default=str)
    sys.stdout.write("\n")
    sys.stdout.flush()


def print_ok(message: str) -> None:
    """Print a success note. Suppressed under --quiet or structured output."""
    if _quiet or is_structured():
        return
    _console.print(f"[bold green]✔ {message}[/bold green]")


def print_warn(message: str) -> None:
    if _quiet:
        return
    _err_console.print(f"[bold yellow]⚠ {message}[/bold yellow]")


def print_error(message: str) -> None:
    """Errors always go to stderr and are never suppressed."""
    _err_console.print(f"[bold red]✖ {message}[/bold red]")


def _normalize(
    data: Mapping[str, Any] | Iterable[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], bool]:
    """Return (rows, was_single_object)."""
    if isinstance(data, Mapping):
        return [dict(data)], True
    return [dict(row) for row in data], False


def _ordered_columns(
    rows: Sequence[Mapping[str, Any]], columns: Sequence[str] | None
) -> list[str]:
    """Column order: explicit if given, else first-seen key order.

    Insertion order is deliberate -- the previous implementation sorted
    alphabetically, which rendered tables as ``created_at, description, id,
    name`` instead of leading with the identifying column.
    """
    if columns is not None:
        return list(columns)
    seen: dict[str, None] = {}
    for row in rows:
        for key in row:
            seen.setdefault(key, None)
    return list(seen)


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list | dict):
        return json.dumps(value, ensure_ascii=False, default=str)
    return str(value)


def render(
    data: Mapping[str, Any] | Iterable[Mapping[str, Any]],
    *,
    columns: Sequence[str] | None = None,
    title: str | None = None,
    empty_message: str = "(no results)",
) -> None:
    """Render structured data in the currently selected output format.

    Accepts either a single mapping (a "show"-style detail view) or an
    iterable of mappings (a "list"-style view). In table mode a single
    mapping is rendered as key/value rows; in the machine-readable formats it
    is emitted as a JSON/YAML object rather than a one-element array, so
    ``dataset show ... -o json | jq .name`` behaves as callers expect.
    """
    rows, single = _normalize(data)

    if _output_format is OutputFormat.JSON:
        emit_json(rows[0] if single and rows else rows)
        return

    if _output_format is OutputFormat.YAML:
        payload: Any = rows[0] if single and rows else rows
        yaml.safe_dump(
            json.loads(json.dumps(payload, default=str)),
            sys.stdout,
            sort_keys=False,
            allow_unicode=True,
            default_flow_style=False,
        )
        sys.stdout.flush()
        return

    if _output_format is OutputFormat.CSV:
        cols = _ordered_columns(rows, columns)
        writer = csv.DictWriter(
            sys.stdout, fieldnames=cols, extrasaction="ignore", lineterminator="\n"
        )
        writer.writeheader()
        for row in rows:
            writer.writerow({c: _stringify(row.get(c)) for c in cols})
        sys.stdout.flush()
        return

    # Table
    if not rows:
        if not _quiet:
            _console.print(empty_message)
        return

    if single:
        table = Table(show_header=True, header_style="bold magenta", title=title)
        table.add_column("field")
        table.add_column("value")
        for key in _ordered_columns(rows, columns):
            table.add_row(key, _stringify(rows[0].get(key)))
        _console.print(table)
        return

    cols = _ordered_columns(rows, columns)
    table = Table(show_header=True, header_style="bold magenta", title=title)
    for col in cols:
        table.add_column(col)
    for row in rows:
        table.add_row(*[_stringify(row.get(col)) for col in cols])
    _console.print(table)
