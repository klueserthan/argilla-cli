"""Dataset management commands."""

from __future__ import annotations

import json
from collections.abc import Iterator
from itertools import islice
from pathlib import Path
from typing import Annotated, Any

import typer

from argilla_cli.context import ctx
from argilla_cli.errors import (
    MissingExtraError,
    ValidationError,
    handle_errors,
    is_classified,
)
from argilla_cli.file_io import atomic_path
from argilla_cli.io_utils import print_ok, print_warn, render
from argilla_cli.options import LimitOpt, WorkspaceOpt, confirm, resolve_workspace_name
from argilla_cli.records_io import (
    ListPolicy,
    RecordFormat,
    compile_mapping,
    filter_completed,
    infer_format,
    iter_dataset_records,
    iter_records,
    load_mapping,
    resolve_target_path,
    spooled_records,
    strip_server_ids,
    transform_record,
    write_records,
)
from argilla_cli.resources import (
    list_datasets,
    resolve_dataset,
    resolve_workspace,
    workspace_name_of,
)

app = typer.Typer(help="Manage Argilla datasets", no_args_is_help=True)

_COLUMNS = ["name", "workspace", "id", "created_at", "description"]

#: How many records `dataset copy` holds in memory at once. The SDK pages the
#: source at a smaller size and batches its own uploads, so this only bounds
#: what the CLI itself accumulates between the two.
_COPY_BATCH_SIZE = 500

#: How many records `dataset push` reads, maps and uploads at a time.
#: Matched to the copy path so both bound their memory the same way.
_PUSH_BATCH_SIZE = 500

MapOpt = Annotated[
    Path | None,
    typer.Option(
        "--map",
        help="JSON mapping file: output_field -> JMESPath expression.",
    ),
]
ListPolicyOpt = Annotated[
    ListPolicy,
    typer.Option(
        "--list-policy",
        help="How to handle mapping expressions that return a list.",
        case_sensitive=False,
    ),
]
ListSepOpt = Annotated[
    str,
    typer.Option("--list-sep", help="Separator used by --list-policy join."),
]


def _row(dataset: Any) -> dict[str, Any]:
    return {
        "name": getattr(dataset, "name", ""),
        "workspace": workspace_name_of(dataset),
        "id": getattr(dataset, "id", ""),
        "created_at": getattr(dataset, "inserted_at", "")
        or getattr(dataset, "created_at", ""),
        "description": getattr(dataset, "description", ""),
    }


def _build_rows(
    dataset: Any,
    *,
    map_file: Path | None,
    flatten: bool,
    completed_only: bool,
    limit: int | None,
    list_policy: ListPolicy,
    list_sep: str,
) -> Any:
    """Produce the record stream for an export, applying every transform once.

    A single pipeline for all output formats, so ``--completed-only`` and
    ``--map`` behave identically whether you export JSONL, CSV or Parquet.
    """
    # JMESPath expressions address the nested record structure, so mapping
    # always runs against unflattened records.
    rows = iter_dataset_records(dataset, flatten=flatten and map_file is None)
    rows = filter_completed(rows, completed_only)

    # The limit caps what is *exported*, so it applies after filtering.
    # Capping the source stream instead meant `--limit 10 --completed-only`
    # read the first ten records and could yield nothing, even with plenty of
    # completed records further down.
    if limit is not None:
        rows = islice(rows, limit)

    if map_file is None:
        return rows

    compiled = compile_mapping(load_mapping(map_file))
    return (transform_record(row, compiled, list_policy, list_sep) for row in rows)


@app.command("list")
@handle_errors
def list_(workspace: WorkspaceOpt = None) -> None:
    """List datasets, optionally restricted to one workspace."""
    rows = [
        _row(ds)
        for ds in list_datasets(ctx.client(), resolve_workspace_name(workspace))
    ]
    render(rows, columns=_COLUMNS)


@app.command("show")
@handle_errors
def show(
    name: Annotated[str, typer.Argument(help="Dataset name")],
    workspace: WorkspaceOpt = None,
) -> None:
    """Show a dataset's fields, questions and record counts."""
    dataset = resolve_dataset(ctx.client(), name, resolve_workspace_name(workspace))
    settings = getattr(dataset, "settings", None)

    detail = _row(dataset)
    detail["guidelines"] = getattr(settings, "guidelines", "") or ""
    detail["fields"] = [
        str(getattr(f, "name", f)) for f in getattr(settings, "fields", [])
    ]
    detail["questions"] = [
        str(getattr(q, "name", q)) for q in getattr(settings, "questions", [])
    ]
    detail["metadata"] = [
        str(getattr(m, "name", m)) for m in getattr(settings, "metadata", [])
    ]
    detail["vectors"] = [
        str(getattr(v, "name", v)) for v in getattr(settings, "vectors", [])
    ]
    try:
        detail.update(dataset.progress())
    except Exception:  # progress is a nice-to-have, not a reason to fail `show`
        pass
    render(detail)


@app.command("progress")
@handle_errors
def progress(
    name: Annotated[str, typer.Argument(help="Dataset name")],
    workspace: WorkspaceOpt = None,
    by_user: Annotated[
        bool,
        typer.Option("--by-user", help="Break the progress down per annotator."),
    ] = False,
) -> None:
    """Show annotation progress for a dataset."""
    dataset = resolve_dataset(ctx.client(), name, resolve_workspace_name(workspace))
    data = dataset.progress(with_users_distribution=by_user)

    users = data.pop("users", None) if by_user else None
    if not users:
        render(dict(data))
        return

    rows = []
    for username, counts in sorted(users.items()):
        completed = counts.get("completed", {})
        pending = counts.get("pending", {})
        rows.append(
            {
                "user": username,
                "completed": sum(completed.values()) if completed else 0,
                "pending": sum(pending.values()) if pending else 0,
                "submitted": completed.get("submitted", 0),
                "draft": completed.get("draft", 0),
                "discarded": completed.get("discarded", 0),
            }
        )
    render(
        rows,
        columns=["user", "completed", "pending", "submitted", "draft", "discarded"],
    )


@app.command("create")
@handle_errors
def create(
    name: Annotated[str, typer.Argument(help="Dataset name")],
    settings_file: Annotated[
        Path,
        typer.Option(
            "--settings",
            "-s",
            help="JSON settings file, as produced by `dataset settings --export`.",
        ),
    ],
    workspace: WorkspaceOpt = None,
) -> None:
    """Create a dataset from an exported settings file."""
    import argilla as rg

    client = ctx.client()
    workspace_name = resolve_workspace_name(workspace)
    if not workspace_name:
        raise ValidationError("a workspace is required; pass --workspace")
    resolve_workspace(client, workspace_name)

    if not settings_file.is_file():
        raise ValidationError(f"settings file not found: {settings_file}")

    # The settings file is user input, so a parse failure is a validation
    # error (13), not the generic exit 1 an unrecognised JSON/pydantic
    # exception would otherwise produce.
    try:
        settings = rg.Settings.from_json(settings_file)
    except Exception as exc:
        if is_classified(exc):
            raise
        raise ValidationError(
            f"could not read settings from {settings_file}: {exc}"
        ) from exc

    dataset = rg.Dataset(
        name=name, workspace=workspace_name, settings=settings, client=client
    ).create()

    render(_row(dataset), columns=_COLUMNS)
    print_ok(f"Created dataset: {name} in workspace '{workspace_name}'")


@app.command("delete")
@handle_errors
def delete(
    name: Annotated[str, typer.Argument(help="Dataset name")],
    workspace: WorkspaceOpt = None,
) -> None:
    """Delete a dataset."""
    dataset = resolve_dataset(ctx.client(), name, resolve_workspace_name(workspace))
    confirm(f"Delete dataset '{name}' (workspace '{workspace_name_of(dataset)}')?")
    dataset.delete()
    print_ok(f"Deleted dataset: {name}")


@app.command("settings")
@handle_errors
def settings_(
    name: Annotated[str, typer.Argument(help="Dataset name")],
    workspace: WorkspaceOpt = None,
    export: Annotated[
        Path | None,
        typer.Option("--export", help="Write the settings to this JSON file."),
    ] = None,
    force: Annotated[
        bool, typer.Option("--force", help="Overwrite an existing export file.")
    ] = False,
) -> None:
    """Show a dataset's settings, or export them for reuse with `create`."""
    dataset = resolve_dataset(ctx.client(), name, resolve_workspace_name(workspace))
    settings = dataset.settings

    if export is not None:
        # `--force` and this message mirror `download`, which had them and
        # this did not. Without it, exporting over an existing file surfaced
        # the SDK's raw FileExistsError as the unclassified exit 1, with no
        # way to say "yes, replace it".
        if export.exists() and not force:
            raise ValidationError(f"{export} already exists; pass --force to overwrite")
        # Written to a temporary sibling and moved into place, so an
        # interrupted export cannot leave a truncated settings file that
        # `dataset create` would later reject.
        with atomic_path(export) as tmp_path:
            # `Settings.to_json` refuses to write to a path that exists, and
            # `atomic_path` hands over a file it has already created.
            tmp_path.unlink()
            settings.to_json(tmp_path)
        print_ok(f"Exported settings to {export}")
        render({"dataset": name, "path": str(export)})
        return

    render(json.loads(json.dumps(settings.serialize(), default=str)))


@app.command("download")
@handle_errors
def download(
    name: Annotated[str, typer.Argument(help="Dataset name")],
    workspace: WorkspaceOpt = None,
    output: Annotated[
        Path | None,
        typer.Option("--output-path", "-O", help="Output file or directory."),
    ] = None,
    fmt: Annotated[
        RecordFormat,
        typer.Option("--fmt", help="Output format.", case_sensitive=False),
    ] = RecordFormat.JSONL,
    map_file: MapOpt = None,
    list_policy: ListPolicyOpt = ListPolicy.JOIN,
    list_sep: ListSepOpt = ", ",
    completed_only: Annotated[
        bool,
        typer.Option(
            "--completed-only", help="Only export records with status=completed."
        ),
    ] = False,
    flatten: Annotated[
        bool,
        typer.Option(
            "--flatten/--no-flatten",
            help="Flatten nested record fields. Ignored when --map is used.",
        ),
    ] = False,
    limit: LimitOpt = None,
    force: Annotated[
        bool, typer.Option("--force", help="Overwrite an existing output file.")
    ] = False,
) -> None:
    """Export a dataset's records to a local file.

    Examples:
        argilla-cli dataset download my-ds -w nlp-lab -O ./my.jsonl
        argilla-cli dataset download my-ds --fmt csv --completed-only
        argilla-cli dataset download my-ds --map mapping.json --list-policy first
    """
    client = ctx.client()
    dataset = resolve_dataset(client, name, resolve_workspace_name(workspace))

    target = resolve_target_path(output, name, fmt, ctx.settings.default_output_dir)
    if target.exists() and not force:
        raise ValidationError(f"{target} already exists; pass --force to overwrite")

    rows = _build_rows(
        dataset,
        map_file=map_file,
        flatten=flatten,
        completed_only=completed_only,
        limit=limit,
        list_policy=list_policy,
        list_sep=list_sep,
    )
    count = write_records(rows, target, fmt)

    print_ok(f"Saved {count} record(s) to {target}")
    render({"path": str(target), "records": count, "format": fmt.value})


@app.command("push")
@handle_errors
def push(
    name: Annotated[str, typer.Argument(help="Dataset name")],
    source: Annotated[
        Path,
        typer.Option("--from", "-f", help="Local jsonl/csv/parquet file to upload."),
    ],
    workspace: WorkspaceOpt = None,
    fmt: Annotated[
        RecordFormat | None,
        typer.Option(
            "--fmt", help="Input format. Inferred from the suffix if omitted."
        ),
    ] = None,
    map_file: MapOpt = None,
    list_policy: ListPolicyOpt = ListPolicy.PRESERVE,
    list_sep: ListSepOpt = ", ",
    limit: LimitOpt = None,
) -> None:
    """Upload records from a local file into an existing dataset.

    The inverse of `download`: the same `--map` JMESPath machinery reshapes
    incoming rows before they are logged. Unlike export, `--list-policy`
    defaults to `preserve` here, because Argilla's structured properties
    (`fields`, `metadata`, `suggestions`, `vectors`) must reach the server as
    mappings and lists rather than flattened strings.
    """
    dataset = resolve_dataset(ctx.client(), name, resolve_workspace_name(workspace))

    # The whole input is parsed and mapped *before* anything is uploaded, and
    # spooled to disk rather than held in memory so a large file still costs
    # only one batch of RAM.
    #
    # Both halves of that matter, and an earlier version had only one. Reading
    # lazily and uploading as it went meant a malformed record at row 600 left
    # the first 500 already on the server -- and since `push` deliberately
    # keeps ids so re-uploading can update records, retrying an input *without*
    # ids then duplicated them. Reading eagerly into a list fixed that but put
    # the whole file back in memory. Spooling gives both.
    #
    # This is only about failures the CLI can see coming: parse errors, bad
    # UTF-8, a JMESPath mapping that blows up. A network failure part-way
    # through an upload still leaves earlier records in place, because
    # `records.log()` chunks internally (batch_size=256) and issues a request
    # per chunk -- there is no transaction to roll back, and never was.
    stream: Iterator[dict[str, Any]] = iter_records(
        source, infer_format(source, fmt), limit
    )
    if map_file is not None:
        compiled = compile_mapping(load_mapping(map_file))
        stream = (
            transform_record(row, compiled, list_policy, list_sep) for row in stream
        )

    with spooled_records(stream) as (count, validated):
        if not count:
            raise ValidationError(f"no records found in {source}")

        while batch := list(islice(validated, _PUSH_BATCH_SIZE)):
            dataset.records.log(batch)

    print_ok(f"Pushed {count} record(s) to '{name}'")
    render({"dataset": name, "records": count, "source": str(source)})


@app.command("copy")
@handle_errors
def copy(
    name: Annotated[str, typer.Argument(help="Source dataset name")],
    target_name: Annotated[str, typer.Argument(help="Name for the new dataset")],
    workspace: WorkspaceOpt = None,
    to_workspace: Annotated[
        str | None,
        typer.Option(
            "--to-workspace", help="Destination workspace. Defaults to the source's."
        ),
    ] = None,
    with_records: Annotated[
        bool,
        typer.Option("--with-records/--no-records", help="Also copy the records."),
    ] = True,
) -> None:
    """Duplicate a dataset, optionally into another workspace."""
    import argilla as rg

    client = ctx.client()
    source = resolve_dataset(client, name, resolve_workspace_name(workspace))

    destination = to_workspace or workspace_name_of(source)
    if not destination:
        raise ValidationError("could not determine the destination workspace")
    resolve_workspace(client, destination)

    # Records are copied in batches so that a large source does not have to
    # fit in memory. The first batch is read *before* the destination is
    # created, which keeps the property that an unreadable source -- a 401, a
    # missing dataset, a dropped connection -- leaves nothing behind at all.
    # Anything that fails later is handled by the rollback below.
    stream = (
        (strip_server_ids(row) for row in iter_dataset_records(source))
        if with_records
        else iter(())
    )
    batch = list(islice(stream, _COPY_BATCH_SIZE))

    new_dataset = rg.Dataset(
        name=target_name,
        workspace=destination,
        settings=source.settings,
        client=client,
    ).create()

    count = 0
    try:
        while batch:
            new_dataset.records.log(batch)
            count += len(batch)
            batch = list(islice(stream, _COPY_BATCH_SIZE))
    except BaseException:
        # BaseException, not Exception: a Ctrl+C part-way through a long copy
        # is precisely when a half-populated dataset gets left behind, and
        # KeyboardInterrupt would slip past the narrower clause. This matches
        # how write_records guards its temporary file.
        #
        # The dataset exists but is empty or partial. Remove it rather than
        # leave an artifact that makes retrying the same name fail.
        try:
            new_dataset.delete()
        except Exception:  # noqa: BLE001 - rollback is best-effort
            print_warn(
                f"Could not remove the partially copied dataset "
                f"'{target_name}'; delete it before retrying."
            )
        raise

    print_ok(f"Copied '{name}' to '{target_name}' in workspace '{destination}'")
    render(
        {
            "source": name,
            "target": target_name,
            "workspace": destination,
            "records": count,
        }
    )


@app.command("to-hub")
@handle_errors
def to_hub(
    name: Annotated[str, typer.Argument(help="Dataset name")],
    repo_id: Annotated[str, typer.Argument(help="Target Hub repo, e.g. user/dataset")],
    workspace: WorkspaceOpt = None,
    with_records: Annotated[
        bool, typer.Option("--with-records/--no-records", help="Include records.")
    ] = True,
    private: Annotated[
        bool, typer.Option("--private", help="Create the Hub repo as private.")
    ] = False,
) -> None:
    """Publish a dataset to the Hugging Face Hub."""
    _require_hub()
    dataset = resolve_dataset(ctx.client(), name, resolve_workspace_name(workspace))

    kwargs: dict[str, Any] = {"with_records": with_records, "private": private}
    token = ctx.settings.hf_token
    if token:
        kwargs["token"] = token

    dataset.to_hub(repo_id, **kwargs)
    print_ok(f"Pushed '{name}' to https://huggingface.co/datasets/{repo_id}")
    render({"dataset": name, "repo_id": repo_id, "with_records": with_records})


@app.command("from-hub")
@handle_errors
def from_hub(
    repo_id: Annotated[str, typer.Argument(help="Source Hub repo, e.g. user/dataset")],
    name: Annotated[
        str | None, typer.Option("--name", help="Name for the new dataset.")
    ] = None,
    workspace: WorkspaceOpt = None,
    with_records: Annotated[
        bool, typer.Option("--with-records/--no-records", help="Include records.")
    ] = True,
    split: Annotated[
        str | None, typer.Option("--split", help="Hub split to import.")
    ] = None,
    subset: Annotated[
        str | None, typer.Option("--subset", help="Hub subset/config to import.")
    ] = None,
) -> None:
    """Import a dataset from the Hugging Face Hub into Argilla."""
    _require_hub()
    import argilla as rg

    client = ctx.client()
    workspace_name = resolve_workspace_name(workspace)
    if workspace_name:
        resolve_workspace(client, workspace_name)

    kwargs: dict[str, Any] = {
        "client": client,
        "with_records": with_records,
        "settings": "auto",
    }
    if name:
        kwargs["name"] = name
    if workspace_name:
        kwargs["workspace"] = workspace_name
    if split:
        kwargs["split"] = split
    if subset:
        kwargs["subset"] = subset
    # The SDK forwards this to snapshot_download and load_dataset, so a token
    # held in a profile rather than the environment still reaches private
    # repos -- matching what `to-hub` already does.
    token = ctx.settings.hf_token
    if token:
        kwargs["token"] = token

    dataset = rg.Dataset.from_hub(repo_id, **kwargs)

    # from_hub is typed Union[Dataset, str]: under settings="ui" it returns a
    # configuration URL instead of a dataset. We always pass "auto", so this
    # is defensive -- but rendering a string as a record row would be worse
    # than saying plainly what came back.
    if isinstance(dataset, str):
        print_ok(f"Argilla needs settings configured for '{repo_id}'")
        render({"repo_id": repo_id, "configure_url": dataset})
        return

    print_ok(f"Imported '{repo_id}' into Argilla")
    render(_row(dataset), columns=_COLUMNS)


def _require_hub() -> None:
    """Fail clearly when the optional Hub dependencies are absent.

    ``jinja2`` is the load-bearing check. The argilla SDK already depends on
    ``datasets`` and ``huggingface_hub``, so testing only those two always
    succeeds even without the extra installed, and the missing template
    dependency then surfaced as a raw ImportError from deep inside the SDK's
    dataset-card rendering. The other two are kept as a cheap guard in case
    that dependency ever changes.
    """
    try:
        import datasets  # noqa: F401
        import huggingface_hub  # noqa: F401
        import jinja2  # noqa: F401
    except ImportError as exc:
        raise MissingExtraError(
            "Hugging Face Hub support", "hub", "argilla-cli[hub]"
        ) from exc
