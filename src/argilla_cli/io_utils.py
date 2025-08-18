from __future__ import annotations

import json
import sys
from typing import Any, Iterable

from rich.console import Console
from rich.table import Table

_console = Console(stderr=False)
_err_console = Console(stderr=True)


def emit_json(obj: Any) -> None:
    json.dump(
        obj,
        sys.stdout,
        ensure_ascii=False,
        indent=None,
        separators=(",", ":"),
    )
    sys.stdout.write("\n")
    sys.stdout.flush()


def print_ok(message: str) -> None:
    _console.print(f"[bold green]✔ {message}[/bold green]")


def print_warn(message: str) -> None:
    _console.print(f"[bold yellow]⚠ {message}[/bold yellow]")


def print_error(message: str) -> None:
    _err_console.print(f"[bold red]✖ {message}[/bold red]")


def print_table(rows: list[dict[str, Any]] | Iterable[dict[str, Any]]) -> None:
    rows = list(rows)
    if not rows:
        _console.print("(no results)")
        return

    columns = sorted({k for row in rows for k in row.keys()})
    table = Table(show_header=True, header_style="bold magenta")
    for col in columns:
        table.add_column(col)
    for row in rows:
        table.add_row(*[str(row.get(col, "")) for col in columns])
    _console.print(table)
