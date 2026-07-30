"""Reading, writing and reshaping dataset records.

Holds the format-specific I/O that ``dataset download`` and ``dataset push``
share, so neither command grows a per-format branch tree. Only Parquet
requires the optional ``export`` extra -- CSV is handled with the standard
library, which the previous implementation needlessly routed through pandas.
"""

from __future__ import annotations

import csv
import json
from collections.abc import Iterable, Iterator
from enum import StrEnum
from pathlib import Path
from typing import Any

from argilla_cli.errors import MissingExtraError, ValidationError


class RecordFormat(StrEnum):
    """Supported on-disk record formats."""

    JSONL = "jsonl"
    CSV = "csv"
    PARQUET = "parquet"


class ListPolicy(StrEnum):
    """What to do when a mapping expression yields a list."""

    JOIN = "join"
    FIRST = "first"
    ERROR = "error"


_SUFFIX_TO_FORMAT = {
    ".jsonl": RecordFormat.JSONL,
    ".ndjson": RecordFormat.JSONL,
    ".json": RecordFormat.JSONL,
    ".csv": RecordFormat.CSV,
    ".parquet": RecordFormat.PARQUET,
    ".pq": RecordFormat.PARQUET,
}


def infer_format(path: Path, explicit: RecordFormat | None) -> RecordFormat:
    """Use the explicit format if given, else infer from the file suffix."""
    if explicit is not None:
        return explicit
    fmt = _SUFFIX_TO_FORMAT.get(path.suffix.lower())
    if fmt is None:
        raise ValidationError(
            f"cannot infer format from {path.name!r}; pass --fmt "
            f"({'|'.join(f.value for f in RecordFormat)})"
        )
    return fmt


def _require_pandas() -> Any:
    try:
        import pandas as pd
    except ImportError as exc:
        raise MissingExtraError(
            "Parquet support", "export", "argilla-cli[export]"
        ) from exc
    return pd


# --------------------------------------------------------------------------
# Mapping (JMESPath)
# --------------------------------------------------------------------------


def load_mapping(path: Path) -> dict[str, str]:
    """Load a JSON mapping of ``output_field -> JMESPath expression``."""
    if not path.exists():
        raise ValidationError(f"mapping file not found: {path}")
    if path.suffix.lower() != ".json":
        raise ValidationError(f"unsupported mapping format {path.suffix!r}; use .json")

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValidationError(f"failed to parse mapping file {path}: {exc}") from exc

    if not isinstance(data, dict):
        raise ValidationError("mapping file must contain a JSON object")
    for key, value in data.items():
        if not isinstance(value, str):
            raise ValidationError(
                f"mapping for {key!r} must be a string JMESPath expression"
            )
    return data


def compile_mapping(mapping: dict[str, str]) -> dict[str, Any]:
    import jmespath

    compiled: dict[str, Any] = {}
    for key, expression in mapping.items():
        try:
            compiled[key] = jmespath.compile(expression)
        except Exception as exc:
            raise ValidationError(
                f"invalid JMESPath for {key!r}: {expression} ({exc})"
            ) from exc
    return compiled


def transform_record(
    record: dict[str, Any],
    compiled: dict[str, Any],
    list_policy: ListPolicy = ListPolicy.JOIN,
    list_sep: str = ", ",
) -> dict[str, Any]:
    """Apply a compiled mapping to one record, flattening to scalar values."""
    out: dict[str, Any] = {}
    for key, expression in compiled.items():
        value = expression.search(record)
        if isinstance(value, list):
            if list_policy is ListPolicy.JOIN:
                out[key] = list_sep.join("" if v is None else str(v) for v in value)
            elif list_policy is ListPolicy.FIRST:
                out[key] = value[0] if value else None
            else:
                raise ValidationError(
                    f"mapping for {key!r} produced a list; use --list-policy join|first"
                )
        elif isinstance(value, dict):
            out[key] = json.dumps(value, ensure_ascii=False, default=str)
        else:
            out[key] = value
    return out


# --------------------------------------------------------------------------
# Record extraction from a live dataset
# --------------------------------------------------------------------------


def iter_dataset_records(
    dataset: Any, *, flatten: bool = False, limit: int | None = None
) -> Iterator[dict[str, Any]]:
    """Yield a dataset's records as plain dicts."""
    records = getattr(dataset, "records", None)
    if records is None:
        raise ValidationError("dataset does not expose records")

    to_list = getattr(records, "to_list", None)
    if not callable(to_list):
        raise ValidationError(
            "records.to_list() is unavailable; upgrade the argilla SDK"
        )

    try:
        rows = to_list(flatten=flatten)
    except Exception as exc:
        raise ValidationError(f"failed to read records: {exc}") from exc

    for index, row in enumerate(rows):
        if limit is not None and index >= limit:
            return
        yield _as_dict(row, index)


def _as_dict(record: Any, index: int) -> dict[str, Any]:
    if isinstance(record, dict):
        return record
    for attr in ("model_dump", "dict"):
        method = getattr(record, attr, None)
        if callable(method):
            try:
                return dict(method())
            except Exception as exc:
                raise ValidationError(
                    f"failed to convert record {index} via {attr}(): {exc}"
                ) from exc
    raise ValidationError(
        f"unexpected record type at index {index}: {type(record).__name__}"
    )


def filter_completed(
    rows: Iterable[dict[str, Any]], completed_only: bool
) -> Iterator[dict[str, Any]]:
    """Keep only completed records when requested.

    Applied uniformly to the dict rows, so ``--completed-only`` behaves the
    same for every output format.
    """
    for row in rows:
        if completed_only and row.get("status") != "completed":
            continue
        yield row


# --------------------------------------------------------------------------
# Writing
# --------------------------------------------------------------------------


def _scalarize(value: Any) -> Any:
    """Flatten a nested value into something a tabular format can hold."""
    if isinstance(value, list | dict):
        return json.dumps(value, ensure_ascii=False, default=str)
    return value


def _unscalarize(value: Any) -> Any:
    """Inverse of :func:`_scalarize`, for reading tabular formats back.

    CSV and Parquet have no nested types, so ``fields``/``metadata``/
    ``suggestions`` are written as JSON text. Without decoding them on the way
    back in, ``download --fmt csv`` followed by ``push --fmt csv`` -- the
    documented inverse workflow -- would hand Argilla strings where it expects
    mappings, and the records would be rejected or silently mistyped.

    Only values that both look like JSON containers *and* parse as one are
    decoded, so ordinary prose is left alone. A text field whose literal
    content is valid JSON (e.g. ``[1, 2]``) does round-trip to a list; that
    ambiguity is inherent to untyped formats, and JSONL is the lossless
    choice when it matters.
    """
    if not isinstance(value, str):
        return value
    candidate = value.strip()
    if not candidate.startswith(("{", "[")):
        return value
    try:
        decoded = json.loads(candidate)
    except ValueError:
        return value
    return decoded if isinstance(decoded, list | dict) else value


def write_records(rows: Iterable[dict[str, Any]], path: Path, fmt: RecordFormat) -> int:
    """Write records to ``path``. Returns the number of records written."""
    path.parent.mkdir(parents=True, exist_ok=True)

    if fmt is RecordFormat.JSONL:
        count = 0
        with path.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
                count += 1
        return count

    materialized = list(rows)

    if fmt is RecordFormat.CSV:
        columns: dict[str, None] = {}
        for row in materialized:
            for key in row:
                columns.setdefault(key, None)
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle, fieldnames=list(columns), extrasaction="ignore"
            )
            writer.writeheader()
            for row in materialized:
                writer.writerow({k: _scalarize(v) for k, v in row.items()})
        return len(materialized)

    pd = _require_pandas()
    frame = pd.DataFrame(
        [{k: _scalarize(v) for k, v in r.items()} for r in materialized]
    )
    try:
        frame.to_parquet(path, index=False)
    except ImportError as exc:
        raise MissingExtraError(
            "Parquet support", "export", "argilla-cli[export]"
        ) from exc
    return len(materialized)


# --------------------------------------------------------------------------
# Reading (for `dataset push`)
# --------------------------------------------------------------------------


def read_records(path: Path, fmt: RecordFormat) -> list[dict[str, Any]]:
    """Read records from a local file."""
    if not path.is_file():
        raise ValidationError(f"input file not found: {path}")

    if fmt is RecordFormat.JSONL:
        rows: list[dict[str, Any]] = []
        with path.open(encoding="utf-8") as handle:
            for line_no, line in enumerate(handle, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValidationError(
                        f"{path}:{line_no}: invalid JSON ({exc})"
                    ) from exc
                if not isinstance(payload, dict):
                    raise ValidationError(f"{path}:{line_no}: expected a JSON object")
                rows.append(payload)
        return rows

    # CSV and Parquet cells are flat, so undo the JSON encoding that
    # write_records applied to nested values.
    if fmt is RecordFormat.CSV:
        with path.open(encoding="utf-8", newline="") as handle:
            return [
                {k: _unscalarize(v) for k, v in row.items()}
                for row in csv.DictReader(handle)
            ]

    pd = _require_pandas()
    frame = pd.read_parquet(path)
    return [
        {k: _unscalarize(v) for k, v in record.items()}
        for record in frame.to_dict(orient="records")
    ]


def resolve_target_path(
    output: Path | None, default_stem: str, fmt: RecordFormat, base_dir: Path
) -> Path:
    """Work out the file to write, expanding directories and bare stems."""
    if output is None:
        return base_dir / f"{default_stem}.{fmt.value}"
    if output.is_dir():
        return output / f"{default_stem}.{fmt.value}"
    if output.suffix == "":
        return output.with_suffix(f".{fmt.value}")
    return output
