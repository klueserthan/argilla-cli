from __future__ import annotations

from pathlib import Path
import warnings
import json
from typing import Any, Dict, Iterable, List, Optional, cast, TYPE_CHECKING

import typer

from argilla_cli.clients.argilla_client import get_client
from argilla_cli.io_utils import emit_json, print_error, print_ok, print_table
from argilla_cli.settings import load_settings
from argilla_cli.errors import (
    exit_with_error,
    NotFoundError,
    ValidationError,
)
from argilla_cli.globals import state

app = typer.Typer(help="Manage Argilla datasets")

if TYPE_CHECKING:  # only for static type checkers; no runtime dependency
    from argilla import Dataset as ArgillaDataset


def _resolve_dataset(
    client: Any, dataset_name: str, workspace: Optional[str]
) -> tuple["ArgillaDataset", str]:
    """Find a dataset by name, optionally constrained to a workspace.

    Returns (dataset, workspace_name) or raises NotFoundError/ValidationError.
    """
    # Collect workspaces once to avoid multiple API calls
    workspaces = list(client.workspaces)  # type: ignore[attr-defined]

    if workspace:
        # Try using the client API to fetch by workspace name directly,
        # but silence the Argilla UserWarning it emits when not found.
        try:
            with warnings.catch_warnings():
                warnings.filterwarnings(
                    "ignore",
                    message=(r"Dataset with name .* not found in workspace .*"),
                    category=UserWarning,
                )
                ds = client.datasets(dataset_name, workspace=workspace)
        except Exception as e:  # surface as not found unless verbose
            msg = f"dataset {dataset_name!r} not found in workspace " f"{workspace!r}"
            raise NotFoundError(msg) from e
        if ds is None:
            msg = f"dataset {dataset_name!r} not found in workspace " f"{workspace!r}"
            raise NotFoundError(msg)
        return ds, workspace

    # No workspace provided: search across all workspaces by calling the API
    matches: list[tuple[Any, str]] = []
    for ws in workspaces:
        ws_name = getattr(ws, "name", "")
        try:
            with warnings.catch_warnings():
                warnings.filterwarnings(
                    "ignore",
                    message=(r"Dataset with name .* not found in workspace .*"),
                    category=UserWarning,
                )
                ds = client.datasets(dataset_name, workspace=ws_name)
        except Exception:
            ds = None
        if ds is not None:
            matches.append((ds, ws_name))

    if not matches:
        raise NotFoundError(f"dataset {dataset_name!r} not found")
    if len(matches) > 1:
        ws_list = ", ".join(ws_name for _, ws_name in matches)
        msg = (
            f"dataset {dataset_name!r} found in multiple workspaces: "
            f"{ws_list}. "
            "Use --workspace to disambiguate."
        )
        raise ValidationError(msg)
    return matches[0]


def _load_mapping_file(path: Path) -> Dict[str, str]:
    """Load JSON mapping: output_field -> JMESPath expression."""
    if not path.exists():
        raise ValidationError(f"mapping file not found: {path}")
    if path.suffix.lower() != ".json":
        raise ValidationError(
            f"unsupported mapping format for {path}. only .json is accepted"
        )

    text = path.read_text(encoding="utf-8")
    try:
        data: Any = json.loads(text)
    except Exception as e:
        raise ValidationError(f"failed to parse mapping file: {e}") from e

    if not isinstance(data, dict):
        raise ValidationError("mapping file must be a JSON object")
    for k, v in data.items():
        if not isinstance(v, str):
            raise ValidationError(
                f"mapping for key {k!r} must be a string JMESPath expression"
            )
    return data  # type: ignore[return-value]


def _compile_mapping(mapping: Dict[str, str]) -> Dict[str, Any]:
    try:
        import jmespath  # type: ignore
    except Exception as e:
        raise ValidationError(
            "jmespath is required for --map. Install with: " "pip install jmespath"
        ) from e

    compiled: Dict[str, Any] = {}
    for key, expr in mapping.items():
        try:
            compiled[key] = jmespath.compile(expr)
        except Exception as e:
            raise ValidationError(f"invalid JMESPath for {key!r}: {expr} ({e})")
    return compiled


def _iter_record_dicts(dataset: Any) -> Iterable[Dict[str, Any]]:
    """Yield records as plain dicts using Argilla's public API.

    This requires Argilla 2.x exposing dataset.records.to_list(flatten=False).
    If the API is unavailable or records cannot be converted deterministically
    to dicts, a ValidationError is raised.
    """
    records_obj = getattr(dataset, "records", None)
    if records_obj is None:
        raise ValidationError("dataset does not expose records")

    to_list = getattr(records_obj, "to_list", None)
    if not callable(to_list):
        raise ValidationError(
            "records.to_list(flatten=False) not available; upgrade argilla"
        )

    try:
        rows = to_list(flatten=False)  # type: ignore[call-arg]
    except Exception as e:
        msg = f"failed to obtain records via to_list: {e}"
        raise ValidationError(msg) from e

    if not isinstance(rows, list):
        # Ensure iterability at runtime and satisfy type checkers
        rows = list(cast(Iterable[Any], rows))

    for idx, rec in enumerate(rows):
        if isinstance(rec, dict):
            yield rec
            continue

        md = getattr(rec, "model_dump", None)
        if callable(md):
            try:
                yield md()  # type: ignore[misc]
                continue
            except Exception as e:
                raise ValidationError(
                    f"failed to convert record {idx} via model_dump: {e}"
                ) from e

        dct = getattr(rec, "dict", None)
        if callable(dct):
            try:
                yield dct()  # type: ignore[misc]
                continue
            except Exception as e:
                raise ValidationError(
                    f"failed to convert record {idx} via dict(): {e}"
                ) from e

        raise ValidationError(
            "unexpected record type at index "
            f"{idx}: {type(rec).__name__}; expected dict or pydantic model"
        )


def _apply_completed_filter(
    records: Iterable[Dict[str, Any]], completed_only: bool
) -> Iterable[Dict[str, Any]]:
    if not completed_only:
        return records
    return (r for r in records if r.get("status") == "completed")


def _transform_record(
    record: Dict[str, Any],
    compiled_mapping: Dict[str, Any],
    list_policy: str,
    list_sep: str,
) -> Dict[str, Any]:
    """Apply compiled mapping to a single record and produce a flat dict.

    list_policy: join|first|error
    """
    out: Dict[str, Any] = {}
    for key, expr in compiled_mapping.items():
        val = expr.search(record)
        if isinstance(val, list):
            if list_policy == "join":
                out[key] = list_sep.join("" if v is None else str(v) for v in val)
            elif list_policy == "first":
                out[key] = val[0] if val else None
            else:
                raise ValidationError(
                    (
                        f"mapping for {key!r} produced a list; "
                        "use --list-policy join|first"
                    )
                )
        elif isinstance(val, dict):
            out[key] = json.dumps(val, ensure_ascii=False)
        else:
            out[key] = val
    return out


@app.command("list")
def list_datasets(
    json_output: bool = typer.Option(
        False,
        "--json/--no-json",
        help="Output JSON",
    ),
) -> None:
    """List all datasets across all workspaces.

    Shows name, id, workspace, created_at, and description.
    """
    try:
        client = get_client(load_settings().settings)
        rows: List[dict[str, Any]] = []
        for ws in client.workspaces:  # type: ignore[attr-defined]
            ws_name = getattr(ws, "name", "")
            for ds in ws.datasets:  # type: ignore[attr-defined]
                rows.append(
                    {
                        "name": getattr(ds, "name", ""),
                        "id": getattr(ds, "id", ""),
                        "workspace": ws_name,
                        "created_at": getattr(ds, "created_at", ""),
                        "description": getattr(ds, "description", ""),
                    }
                )
    except Exception as e:
        exit_with_error(e, verbose=state.verbose)
        return

    if state.json_output or json_output:
        emit_json(rows)
    else:
        print_table(rows)


@app.command("download")
def download_dataset(
    dataset_name: str = typer.Argument(..., help="Dataset name"),
    workspace: Optional[str] = typer.Option(
        None,
        "--workspace",
        help="Workspace name",
    ),
    map_file: Optional[Path] = typer.Option(
        None,
        "--map",
        help=(
            "Path to JSON mapping file (output_field -> JMESPath expression). "
            "When set, a mapped version of the dataset is saved."
        ),
    ),
    fmt: str = typer.Option(
        "jsonl",
        "--fmt",
        help="Output format: jsonl|csv|parquet",
    ),
    split: Optional[str] = typer.Option(
        None, "--split", help="Dataset split (if applicable)"
    ),
    output: Optional[Path] = typer.Option(
        None,
        "--output",
        help="Output file or directory path",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Overwrite existing output",
    ),
    json_output: bool = typer.Option(
        False,
        "--json/--no-json",
        help="Output JSON",
    ),
    completed_only: bool = typer.Option(
        False,
        "--completed-only/--no-completed-only",
        help="Only include records with status=completed",
    ),
) -> None:
    """Download/export a dataset from Argilla.

        Examples:
            argilla-cli dataset download my-ds \
                --workspace nlp-lab --output ./my.jsonl
    """
    client = get_client(load_settings().settings)
    try:
        dataset, _ws_name = _resolve_dataset(client, dataset_name, workspace)
    except Exception as e:
        exit_with_error(e, verbose=state.verbose)
        return

    # Build an iterator of plain dict records and optionally filter
    records_iter = _iter_record_dicts(dataset)
    records_iter = _apply_completed_filter(records_iter, completed_only)

    # Decide output path
    if output is None:
        if split:
            output = Path.cwd() / dataset_name
        else:
            output = Path.cwd() / f"{dataset_name}.{fmt}"

    # Compute final target file path:
    # - If output is a directory, place dataset_name.<fmt> inside it
    # - If output has no suffix, append .<fmt>
    target_path = output
    try:
        if target_path.exists() and target_path.is_dir():
            target_path = target_path / f"{dataset_name}.{fmt}"
        elif target_path.suffix == "":
            target_path = target_path.with_suffix(f".{fmt}")
    except Exception:
        # If the path doesn't exist, infer by suffix only
        if target_path.suffix == "":
            target_path = target_path.with_suffix(f".{fmt}")

    # Existence check
    if target_path.exists() and not force:
        print_error("Output path exists; use --force to overwrite.")
        raise typer.Exit(code=13)

    target_path.parent.mkdir(parents=True, exist_ok=True)

    paths: List[str] = []

    try:
        if fmt == "jsonl":
            # Write JSONL; if mapping is provided, transform rows first
            if map_file is not None:
                mapping = _load_mapping_file(map_file)
                compiled = _compile_mapping(mapping)
                list_policy = "join"
                list_sep = ", "
                with target_path.open("w", encoding="utf-8") as f:
                    for row in records_iter:
                        out_row = _transform_record(
                            row, compiled, list_policy, list_sep
                        )
                        f.write(json.dumps(out_row, ensure_ascii=False) + "\n")
                paths = [str(target_path)]
            else:
                with target_path.open("w", encoding="utf-8") as f:
                    for row in records_iter:
                        f.write(json.dumps(row, ensure_ascii=False) + "\n")
                paths = [str(target_path)]
        elif fmt == "csv":
            if map_file is not None:
                mapping = _load_mapping_file(map_file)
                compiled = _compile_mapping(mapping)
                list_policy = "join"
                list_sep = ", "
                rows: List[Dict[str, Any]] = []
                for row in records_iter:
                    rows.append(_transform_record(row, compiled, list_policy, list_sep))
                try:
                    import pandas as pd  # type: ignore
                except Exception as e:
                    print_error(
                        "CSV export requires pandas. Install with: "
                        "pip install pandas"
                    )
                    raise typer.Exit(code=13) from e
                df = pd.DataFrame(rows)
            else:
                # Use Argilla's flatten=True for speed
                # mypy: ignore[attr-defined]
                records_to_list = dataset.records.to_list
                records_list = records_to_list(flatten=True)
                try:
                    import pandas as pd  # type: ignore
                except Exception as e:
                    print_error(
                        "CSV export requires pandas. Install with: "
                        "pip install pandas"
                    )
                    raise typer.Exit(code=13) from e
                df = pd.DataFrame(records_list)
                if completed_only:
                    if "status" not in df.columns:
                        raise ValidationError(
                            "status field not available; cannot use " "--completed-only"
                        )
                    df = df[df["status"] == "completed"]
            df.to_csv(target_path, index=False)
            paths = [str(target_path)]
        elif fmt == "parquet":
            if map_file is not None:
                mapping = _load_mapping_file(map_file)
                compiled = _compile_mapping(mapping)
                list_policy = "join"
                list_sep = ", "
                rows: List[Dict[str, Any]] = []
                for row in records_iter:
                    rows.append(_transform_record(row, compiled, list_policy, list_sep))
                try:
                    import pandas as pd  # type: ignore
                except Exception as e:
                    print_error(
                        "Parquet export requires pandas. Install with: "
                        "pip install pandas"
                    )
                    raise typer.Exit(code=13) from e
                df = pd.DataFrame(rows)
            else:
                # mypy: ignore[attr-defined]
                records_to_list = dataset.records.to_list
                records_list = records_to_list(flatten=True)
                try:
                    import pandas as pd  # type: ignore
                except Exception as e:
                    print_error(
                        "Parquet export requires pandas. Install with: "
                        "pip install pandas"
                    )
                    raise typer.Exit(code=13) from e
                df = pd.DataFrame(records_list)
                if completed_only:
                    if "status" not in df.columns:
                        raise ValidationError(
                            "status field not available; cannot use " "--completed-only"
                        )
                    df = df[df["status"] == "completed"]
            try:
                df.to_parquet(target_path, index=False)
            except Exception:  # pragma: no cover - platform dependent
                print_error("Parquet export requires a parquet engine (e.g., pyarrow).")
                raise typer.Exit(code=13)
            paths = [str(target_path)]
        else:
            print_error("Unsupported format; choose from jsonl,csv,parquet")
            raise typer.Exit(code=2)
    except Exception as e:
        print_error(str(e))
        raise typer.Exit(code=11)

    if state.json_output or json_output:
        emit_json(paths)
    else:
        for p in paths:
            print_ok(f"Saved: {p}")
