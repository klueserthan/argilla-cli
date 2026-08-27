"""Doing the annotation work, rather than administering it.

Every other command group in this CLI is effectively owner/admin-only,
because the SDK paths they use are: ``dataset.records`` and ``records.log()``
both hand back or write *every* user's responses, so the server refuses them
to an annotator key. That leaves the one job Argilla exists for -- labelling
records -- as the one job this CLI could not do.

These commands go through :mod:`argilla_cli.annotation_api`, the seam over
the ``/me`` endpoints the web UI itself uses. The server opens those to any
member of the dataset's workspace and binds every response to the
authenticated caller, so *one* command set works unchanged under an
annotator, admin or owner key: the key decides whose responses are read and
written, and nothing here has to know which role it holds.

The shape of the group follows from that. ``next`` hands out a record and
``submit``/``discard`` answer one by id, because the caller -- a human RA
working a queue, or an agent doing the same -- holds no state between
invocations beyond the record id it was just given. That id is the server's
own record id, straight out of ``next``'s output, which is why it is a
positional argument rather than something the CLI could look up: outside the
web UI there is no other handle on an individual record.

Answers arrive either as repeated ``--answer question=value`` pairs, which is
what a shell or an agent writes inline, or as one JSON object via ``--from``,
which is what a multi-question or structured answer needs (a ranking, a span,
a multi-label list). The two are deliberately mutually exclusive: merging
them would make the precedence a thing to remember, and there is no answer
that needs both.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any

import typer

from argilla_cli.annotation_api import get_record, search_my_records, submit_response
from argilla_cli.context import ctx
from argilla_cli.errors import NotFoundError, ValidationError, handle_errors
from argilla_cli.file_io import read_text_file
from argilla_cli.io_utils import print_ok, render
from argilla_cli.options import LimitOpt, WorkspaceOpt, resolve_workspace_name
from argilla_cli.resources import resolve_dataset

app = typer.Typer(help="Annotate records as the calling user", no_args_is_help=True)

#: One record, not a page. `next` exists to hand out the thing to work on
#: next, and a queue is worked one record at a time; `--limit` is there for
#: callers that want to look ahead or batch.
_DEFAULT_LIMIT = 1

_COLUMNS = [
    "id",
    "status",
    "fields",
    "suggestions",
    "my_responses",
    "pending_total",
]

#: Read from stdin rather than from a file with this name, matching the
#: convention every other tool uses for it.
_STDIN = "-"


class ResponseStatus(StrEnum):
    """The statuses ``annotate submit`` may set.

    ``discarded`` is deliberately absent: it takes no values, so allowing it
    here would mean a ``--status discarded --answer label=x`` whose answers
    are silently dropped. It has its own command instead, and Typer rejects
    the value with a usage error (exit 2).
    """

    SUBMITTED = "submitted"
    DRAFT = "draft"


AnswerOpt = Annotated[
    list[str] | None,
    typer.Option(
        "--answer",
        help="question=value. Repeatable. A JSON value is sent structured.",
    ),
]
AnswerFileOpt = Annotated[
    Path | None,
    typer.Option(
        "--from",
        help=(
            "JSON file mapping question name to value, or - for stdin. "
            "Mutually exclusive with --answer."
        ),
    ),
]
RecordIdArg = Annotated[
    str, typer.Argument(help="Record id, as shown by `annotate next`.")
]
DatasetArg = Annotated[str, typer.Argument(help="Dataset name")]


def _dataset_id(client: Any, name: str, workspace: str | None) -> tuple[Any, str]:
    """Resolve the dataset, and return it with the server id to address it by."""
    dataset = resolve_dataset(client, name, workspace)
    return dataset, str(getattr(dataset, "id", "") or "")


def _writable_record(client: Any, record_id: str, name: str, dataset_id: str) -> None:
    """Refuse to answer a record that is not in the dataset the caller named.

    ``POST /api/v1/records/{record_id}/responses`` is scoped to the record
    alone -- nothing in the request mentions a dataset. So without this
    check the dataset argument would be decorative on a write, and pairing
    dataset A's name with a record id from dataset B (any dataset in a
    workspace the key can reach) would answer B's record and report success
    under A's name. Reading is not exposed the same way: `next` addresses the
    dataset itself, and the server picks the records.

    A mismatch is a ``NotFoundError`` rather than a validation error because
    that is what it is from the caller's stated intent -- there is no such
    record in that dataset -- and it keeps one verdict for the whole
    condition: a record id that exists nowhere already arrives here as the
    lookup's own 404, which ``map_exception`` maps to the same exit 12.
    """
    record = get_record(client, record_id)
    # Both sides are UUIDs that only ever meet as text; the SDK renders them
    # lowercase and so does the API, but a case difference must not read as a
    # different dataset.
    if str(record.get("dataset_id", "")).lower() != dataset_id.lower():
        raise NotFoundError(f"record {record_id} not found in dataset {name!r}")


def _coerce(raw: str) -> Any:
    """A JSON value if it parses as one, otherwise the literal string.

    Argilla's questions are not all text: a rating takes a number, a
    multi-label takes a list, a span takes objects. Quoting every label as
    JSON would make the common case (``label=positive``) unusable from a
    shell, so the string is the fallback rather than the rule.
    """
    try:
        return json.loads(raw)
    except ValueError:
        return raw


def _parse_answers(answers: Sequence[str]) -> dict[str, Any]:
    """Turn ``question=value`` pairs into the values mapping.

    Split on the *first* ``=`` only: free-text answers contain them, and
    splitting on all of them would truncate the answer rather than fail
    loudly.
    """
    values: dict[str, Any] = {}
    for item in answers:
        name, separator, raw = item.partition("=")
        if not separator or not name:
            raise ValidationError(f"--answer expects question=value, got {item!r}")
        values[name] = _coerce(raw)
    return values


def _load_answers(source: Path) -> dict[str, Any]:
    """Read the answers object from a file or stdin.

    File contents go through ``read_text_file`` so one undecodable byte is a
    validation error (13) rather than the ``UnicodeDecodeError`` that slips
    past ``map_exception`` as a generic exit 1.
    """
    if str(source) == _STDIN:
        text = sys.stdin.read()
        description = "the answers on stdin"
    else:
        description = f"answer file {source}"
        text = read_text_file(source, description)

    try:
        payload = json.loads(text)
    except ValueError as exc:
        raise ValidationError(f"{description} is not valid JSON: {exc}") from exc

    if not isinstance(payload, dict):
        raise ValidationError(
            f"{description} must be a JSON object mapping question name to value"
        )
    return payload


def _answer_values(
    answers: Sequence[str] | None, source: Path | None
) -> dict[str, Any]:
    """The answers to send, from exactly one of the two sources."""
    if answers and source is not None:
        raise ValidationError("pass either --answer or --from, not both")
    if source is not None:
        values = _load_answers(source)
    elif answers:
        values = _parse_answers(answers)
    else:
        raise ValidationError(
            "no answers given; pass --answer question=value or --from FILE"
        )
    if not values:
        raise ValidationError("no answers given; a submitted response needs values")
    return values


def _record_row(record: dict[str, Any], pending_total: int) -> dict[str, Any]:
    """One record as a row, with its nested structures left nested.

    ``responses`` is renamed to ``my_responses`` because that is what the
    ``/me`` search returns and the distinction is the whole point of using
    it: the admin route would have put every annotator's answers here.

    The server's total rides on every row rather than wrapping the list in a
    detail object. Wrapping would render the records as one JSON blob in a
    table cell, and dropping it would leave ``-o json`` -- the format an
    agent actually reads -- unable to see how much work is left.
    """
    return {
        "id": record.get("id", ""),
        "status": record.get("status", ""),
        "fields": record.get("fields", {}),
        "suggestions": record.get("suggestions", []),
        "my_responses": record.get("responses", []),
        "pending_total": pending_total,
    }


@app.command("next")
@handle_errors
def next_(
    name: DatasetArg,
    workspace: WorkspaceOpt = None,
    limit: LimitOpt = _DEFAULT_LIMIT,
) -> None:
    """Show the next record(s) waiting for the calling user's response.

    Examples:
        argilla-cli annotate next my-ds -w nlp-lab
        argilla-cli -o json annotate next my-ds -w nlp-lab --limit 5
    """
    client = ctx.client()
    _, dataset_id = _dataset_id(client, name, resolve_workspace_name(workspace))

    records, total = search_my_records(
        client, dataset_id, limit=limit or _DEFAULT_LIMIT, pending_only=True
    )
    rows = [_record_row(record, total) for record in records]

    if not rows:
        print_ok(f"No pending records in '{name}'")
    else:
        print_ok(f"{len(rows)} of {total} pending record(s) in '{name}'")
    render(rows, columns=_COLUMNS)


@app.command("submit")
@handle_errors
def submit(
    name: DatasetArg,
    record_id: RecordIdArg,
    workspace: WorkspaceOpt = None,
    answer: AnswerOpt = None,
    source: AnswerFileOpt = None,
    status: Annotated[
        ResponseStatus,
        typer.Option(
            "--status",
            help="Response status to record.",
            case_sensitive=False,
        ),
    ] = ResponseStatus.SUBMITTED,
) -> None:
    """Answer one record as the calling user.

    Examples:
        argilla-cli annotate submit my-ds <record-id> --answer label=positive
        argilla-cli annotate submit my-ds <record-id> --answer rating=4 --status draft
        argilla-cli annotate submit my-ds <record-id> --from answers.json
    """
    client = ctx.client()
    # Validate the answers before the lookup so a malformed --answer is
    # reported without a round trip to the server.
    values = _answer_values(answer, source)
    _, dataset_id = _dataset_id(client, name, resolve_workspace_name(workspace))
    _writable_record(client, record_id, name, dataset_id)

    submit_response(client, record_id, values=values, status=status.value)

    print_ok(f"Recorded a {status.value} response for record {record_id}")
    render(
        {
            "dataset": name,
            "record_id": record_id,
            "status": status.value,
            "questions": sorted(values),
        }
    )


@app.command("discard")
@handle_errors
def discard(
    name: DatasetArg,
    record_id: RecordIdArg,
    workspace: WorkspaceOpt = None,
) -> None:
    """Discard one record: an answer that it should not be annotated.

    Not gated behind ``confirm()``, unlike the ``delete`` commands. This
    writes the caller's *own* response and destroys nothing shared -- another
    annotator's answers, the record, the dataset are all untouched, and the
    status can be replaced by submitting one afterwards. Prompting would also
    strand an agent working a queue with no terminal to answer on.

    Examples:
        argilla-cli annotate discard my-ds <record-id> -w nlp-lab
    """
    client = ctx.client()
    _, dataset_id = _dataset_id(client, name, resolve_workspace_name(workspace))
    _writable_record(client, record_id, name, dataset_id)

    submit_response(client, record_id, values=None, status="discarded")

    print_ok(f"Discarded record {record_id}")
    render({"dataset": name, "record_id": record_id, "status": "discarded"})
