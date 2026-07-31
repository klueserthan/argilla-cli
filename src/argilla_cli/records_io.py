"""Reading, writing and reshaping dataset records.

Holds the format-specific I/O that ``dataset download`` and ``dataset push``
share, so neither command grows a per-format branch tree. Only Parquet
requires the optional ``export`` extra -- CSV is handled with the standard
library, which the previous implementation needlessly routed through pandas.
"""

from __future__ import annotations

import csv
import json
import tempfile
from collections.abc import Iterable, Iterator
from enum import StrEnum
from pathlib import Path
from typing import Any

from argilla_cli.atomic_io import atomic_path
from argilla_cli.errors import MissingExtraError, ValidationError, is_classified


class RecordFormat(StrEnum):
    """Supported on-disk record formats."""

    JSONL = "jsonl"
    CSV = "csv"
    PARQUET = "parquet"


class ListPolicy(StrEnum):
    """What a mapping expression's container results collapse to.

    ``join``/``first``/``error`` are export policies: they flatten lists (and
    JSON-encode dicts) so the result fits a tabular cell. ``preserve`` keeps
    containers intact, which is what building structured records for upload
    needs -- Argilla properties such as ``fields``, ``metadata``,
    ``suggestions`` and ``vectors`` are mappings and lists, not strings.
    """

    JOIN = "join"
    FIRST = "first"
    ERROR = "error"
    PRESERVE = "preserve"


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
    """Apply a compiled mapping to one record.

    Under every policy but ``preserve`` the result is flattened to scalars,
    which is right for export and wrong for upload: a mapping that builds a
    ``fields`` dict must stay a dict for ``records.log()`` to accept it.
    """
    out: dict[str, Any] = {}
    for key, expression in compiled.items():
        value = expression.search(record)
        if list_policy is ListPolicy.PRESERVE:
            out[key] = value
        elif isinstance(value, list):
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
    """Yield a dataset's records as plain dicts.

    Prefers the SDK's lazy record iterator, which pages through the dataset
    and only fetches the next batch on demand. That matters because callers
    stop early: ``--limit 10`` against a million-record dataset used to pull
    the whole thing into memory first, since ``to_list()`` materialises
    everything before any downstream slicing runs. Laziness composes with the
    filter-then-limit pipeline in ``_build_rows``, so ``--completed-only
    --limit N`` keeps pulling until it has N *matching* records, and no
    further.

    ``to_list`` remains the path when ``flatten`` is requested, since only it
    can produce the dotted-key form.

    Streaming goes through ``iter(records)`` rather than ``records()``. Both
    return the SDK's lazy iterator, but iteration is the narrower contract:
    it depends only on the accessor being iterable, not on the signature of
    its ``__call__``. ``test_sdk_contract`` pins that assumption against the
    real SDK so a future change is caught here rather than in the field.
    """
    records = getattr(dataset, "records", None)
    if records is None:
        raise ValidationError("dataset does not expose records")

    if hasattr(records, "__iter__"):
        flattener = _record_flattener() if flatten else None
        if flattener is not None or not flatten:
            yield from _stream_records(records, limit, flattener)
            return

    to_list = getattr(records, "to_list", None)
    if not callable(to_list):
        raise ValidationError(
            "records.to_list() is unavailable; upgrade the argilla SDK"
        )

    try:
        rows = to_list(flatten=flatten)
    except Exception as exc:
        # Fetching records is a network call. An auth or transport failure
        # already carries its own meaning, and re-labelling it as a
        # validation error would report a 401 as exit 13 instead of 10. Only
        # genuinely unrecognised failures get the record-reading context.
        if is_classified(exc):
            raise
        raise ValidationError(f"failed to read records: {exc}") from exc

    for index, row in enumerate(rows):
        if limit is not None and index >= limit:
            return
        yield _as_dict(row, index)


def _record_flattener() -> Any | None:
    """The SDK's per-record flattener, if this version exposes one.

    ``to_list(flatten=True)`` is just a loop over this same function, so
    applying it to a streamed record produces byte-identical rows -- which
    means ``--flatten`` can be lazy too, instead of materialising the whole
    dataset before ``--limit`` has any say.

    It is a private helper, so its absence is treated as "no lazy flatten
    available" rather than an error, and the eager ``to_list`` path is used
    instead. ``test_sdk_contract`` fails loudly if it moves, so the
    degradation is noticed rather than silently shipped.
    """
    try:
        from argilla.records._io._generic import GenericIO
    except ImportError:  # pragma: no cover - depends on SDK internals
        return None
    flattener = getattr(GenericIO, "_record_to_dict", None)
    return flattener if callable(flattener) else None


def _stream_records(
    records: Any, limit: int | None = None, flattener: Any | None = None
) -> Iterator[dict[str, Any]]:
    """Page through the dataset's records, fetching only what is consumed."""
    try:
        iterator = iter(records)
    except Exception as exc:
        if is_classified(exc):
            raise
        raise ValidationError(f"failed to read records: {exc}") from exc

    index = 0
    while True:
        if limit is not None and index >= limit:
            return
        try:
            record = next(iterator)
        except StopIteration:
            return
        except Exception as exc:
            # Each batch is a separate request, so a failure part-way through
            # is still a network event and must keep its own exit code.
            if is_classified(exc):
                raise
            raise ValidationError(f"failed to read records: {exc}") from exc

        if flattener is None:
            yield _as_dict(record, index)
        else:
            try:
                # SDK records go through the SDK's own flattener, so the rows
                # match to_list(flatten=True) exactly. A record that is
                # already a plain mapping is flattened directly.
                if isinstance(record, dict):
                    yield _flatten_mapping(record)
                else:
                    yield _flatten_mapping(dict(flattener(record, True)))
            except Exception as exc:
                raise ValidationError(
                    f"failed to flatten record {index}: {exc}"
                ) from exc
        index += 1


def _flatten_mapping(row: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    """Collapse nested mappings into dotted keys, as ``flatten=True`` does."""
    flat: dict[str, Any] = {}
    for key, value in row.items():
        name = f"{prefix}{key}"
        if isinstance(value, dict):
            flat.update(_flatten_mapping(value, prefix=f"{name}."))
        else:
            flat[name] = value
    return flat


def _as_dict(record: Any, index: int) -> dict[str, Any]:
    if isinstance(record, dict):
        return record
    for attr in ("to_dict", "model_dump", "dict"):
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


#: Keys the server owns and that must not be carried between datasets.
SERVER_OWNED_KEYS = ("id", "_server_id")


def strip_server_ids(record: dict[str, Any]) -> dict[str, Any]:
    """Drop server-assigned identifiers so a record logs as a new one.

    ``records.log()`` is an upsert: the SDK documents that "if the record
    includes a known ``id`` field, the record will be updated". Carrying a
    source dataset's ids into a copy therefore asks the server to update
    records that belong to a different dataset instead of creating
    independent ones.

    Deliberately not applied to ``push``: there the file is the user's own,
    and re-uploading with ids to update existing records is a legitimate
    workflow rather than an accident.
    """
    return {k: v for k, v in record.items() if k not in SERVER_OWNED_KEYS}


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


def _is_missing(value: Any) -> bool:
    """True for a tabular cell that stands in for an absent key.

    ``csv.DictWriter`` fills a column a given row lacks with ``""``, and
    pandas fills it with ``NaN``. Restoring those as real values would send
    ``metadata: ""`` to Argilla for records that simply had no metadata,
    which is not a mapping and can get the whole ``records.log()`` batch
    rejected. Neither format can distinguish "absent" from "empty", so the
    round-trip treats them alike; JSONL preserves the difference.
    """
    # Tested scalar-first and without ever comparing the value itself with
    # ``==``. A Parquet file with a native list column yields NumPy arrays,
    # and ``array == ""`` returns an array whose truth value raises.
    if value is None:
        return True
    if isinstance(value, float):
        return value != value  # NaN
    return isinstance(value, str) and value == ""


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
    """Write records to ``path`` atomically. Returns the number written.

    The export goes to a temporary sibling and only replaces the target once
    it has finished. Writing straight to the destination truncated it the
    moment the file was opened, so a stream that failed part-way left a
    half-written export in place -- and with ``--force``, destroyed a
    previously good one before knowing the replacement would succeed.
    """
    with atomic_path(path) as tmp_path:
        return _write_to(rows, tmp_path, fmt)


def _write_to(rows: Iterable[dict[str, Any]], path: Path, fmt: RecordFormat) -> int:
    """Serialise records to ``path``. Callers handle atomicity."""
    if fmt is RecordFormat.JSONL:
        count = 0
        with path.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
                count += 1
        return count

    if fmt is RecordFormat.CSV:
        return _write_csv(rows, path)
    return _write_parquet(rows, path)


def _write_csv(rows: Iterable[dict[str, Any]], path: Path) -> int:
    """Write CSV without holding the whole dataset in memory.

    CSV needs its header first, and the header is the union of every row's
    keys -- which is only known after the last row has been seen. Collecting
    the records to learn it would undo the lazy record iterator on the one
    format most likely to be pointed at a large export: the rows streamed in
    a batch at a time and then all sat in RAM anyway.

    So rows are spooled to a scratch file as they arrive, carrying only the
    column names in memory, and replayed once the union is complete. Peak
    memory becomes proportional to the number of *columns* rather than the
    number of records. The cost is transient disk roughly the size of the
    export, taken in the destination's own filesystem where that space is
    what the user is already spending.

    Keys are stringified on the way in so that the header and the replayed
    rows agree: JSON object keys are always strings, so a non-string key
    would come back as one and no longer match its own column. The written
    output is unchanged, since ``csv`` stringifies field names anyway.
    """
    columns: dict[str, None] = {}
    count = 0

    # Unlinked immediately on POSIX, so it never appears in the output
    # directory even while it is being written.
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8", dir=path.parent) as spool:
        for row in rows:
            scalar = {str(k): _scalarize(v) for k, v in row.items()}
            for key in scalar:
                columns.setdefault(key, None)
            spool.write(json.dumps(scalar, ensure_ascii=False, default=str) + "\n")
            count += 1

        spool.seek(0)
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle, fieldnames=list(columns), extrasaction="ignore"
            )
            writer.writeheader()
            for line in spool:
                writer.writerow(json.loads(line))

    return count


def _write_parquet(rows: Iterable[dict[str, Any]], path: Path) -> int:
    """Write Parquet, which is columnar and so needs the full set of rows.

    Unlike CSV this cannot stream: pandas builds the frame before any of it
    is encoded. Writing row groups incrementally would need the schema fixed
    up front, which the union-of-columns behaviour does not have. Callers
    exporting something too large to hold should choose JSONL, which streams
    end to end.
    """
    pd = _require_pandas()
    materialized = [{k: _scalarize(v) for k, v in r.items()} for r in rows]
    frame = pd.DataFrame(materialized)
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


def read_records(
    path: Path, fmt: RecordFormat, limit: int | None = None
) -> list[dict[str, Any]]:
    """Read records from a local file, stopping once ``limit`` are collected.

    The text formats stop parsing at the limit rather than reading everything
    and slicing afterwards. That keeps ``push --limit 5`` cheap on a large
    file, and means malformed content *after* the fifth record cannot fail an
    upload that was never going to read it.

    Parquet is columnar, so pandas materialises the file regardless; the
    limit is applied after the read there.
    """
    if not path.is_file():
        raise ValidationError(f"input file not found: {path}")

    if fmt is RecordFormat.JSONL:
        rows: list[dict[str, Any]] = []
        with path.open(encoding="utf-8") as handle:
            for line_no, line in enumerate(handle, start=1):
                if limit is not None and len(rows) >= limit:
                    break
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
    # write_records applied to nested values, and drop the filler cells
    # that stand in for keys a given row never had.
    if fmt is RecordFormat.CSV:
        csv_rows: list[dict[str, Any]] = []
        with path.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                if limit is not None and len(csv_rows) >= limit:
                    break
                csv_rows.append(_restore_row(row))
        return csv_rows

    pd = _require_pandas()
    try:
        frame = pd.read_parquet(path)
    except ImportError as exc:
        # pandas can be present while no Parquet engine is. Mirror the write
        # path so this exits 13 with install guidance, not a generic 1.
        raise MissingExtraError(
            "Parquet support", "export", "argilla-cli[export]"
        ) from exc
    except Exception as exc:
        # A corrupt or mislabelled file raises an engine-specific decoding
        # error (pyarrow.lib.ArrowInvalid and friends) that the mapper has no
        # reason to recognise. It is bad user input, so name it as such.
        if is_classified(exc):
            raise
        raise ValidationError(f"could not read Parquet file {path}: {exc}") from exc

    records = frame.to_dict(orient="records")
    if limit is not None:
        records = records[:limit]
    return [_restore_row(record) for record in records]


def _restore_row(row: dict[str, Any]) -> dict[str, Any]:
    """Rebuild one record from a tabular row."""
    return {k: _unscalarize(v) for k, v in row.items() if not _is_missing(v)}


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
