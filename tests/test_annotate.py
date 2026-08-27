"""CLI tests for ``argilla_cli.commands.annotate``.

These commands are the only ones in the CLI that go down the hand-rolled
annotator-grade seam rather than the SDK, so the tests assert on the *wire*
as well as the exit code: which URL was called, with which query parameters,
and what body went out. Nothing on this side validates the request, so a
drifted body would otherwise only fail against a live server.

Every test that exercises a command asks for ``fake_api``: without it the
fake client exposes no HTTP transport and the seam correctly reports exit 11.
"""

from __future__ import annotations

import json as jsonlib
from typing import Any

import httpx
from typer.testing import CliRunner

from argilla_cli.main import app

from .conftest import FakeAPI, FakeArgilla

RECORD_ID = "22222222-2222-2222-2222-222222222222"


def _dataset_id(
    client: FakeArgilla, name: str = "reviews", workspace: str = "nlp-lab"
) -> str:
    return str(client.datasets(name, workspace=workspace).id)


def _search_path(client: FakeArgilla) -> str:
    return f"/api/v1/me/datasets/{_dataset_id(client)}/records/search"


def _responses_path(record_id: str = RECORD_ID) -> str:
    return f"/api/v1/records/{record_id}/responses"


def _body(request: httpx.Request) -> dict[str, Any]:
    return jsonlib.loads(request.content.decode("utf-8"))


def _a_page(total: int = 7) -> dict[str, Any]:
    return {
        "items": [
            {
                "record": {
                    "id": RECORD_ID,
                    "status": "pending",
                    "fields": {"text": "hello"},
                    "responses": [
                        {"values": {"label": {"value": "neutral"}}, "status": "draft"}
                    ],
                    "suggestions": [{"question_name": "label", "value": "positive"}],
                },
                "query_score": None,
            }
        ],
        "total": total,
    }


# ---------------------------------------------------------------------------
# next
# ---------------------------------------------------------------------------


def test_next_renders_the_record_with_its_fields_suggestions_and_my_responses(
    runner: CliRunner,
    credentials: None,
    fake_client: FakeArgilla,
    fake_api: FakeAPI,
) -> None:
    """One row per record, with everything an annotator needs to answer it.

    The nested structures stay nested so ``-o json`` is directly usable.
    """
    fake_api.route("POST", _search_path(fake_client), json=_a_page())

    result = runner.invoke(
        app, ["-o", "json", "annotate", "next", "reviews", "-w", "nlp-lab"]
    )

    assert result.exit_code == 0, result.output
    rows = jsonlib.loads(result.stdout)
    assert len(rows) == 1
    assert rows[0]["id"] == RECORD_ID
    assert rows[0]["status"] == "pending"
    assert rows[0]["fields"] == {"text": "hello"}
    assert rows[0]["suggestions"] == [{"question_name": "label", "value": "positive"}]
    assert rows[0]["my_responses"] == [
        {"values": {"label": {"value": "neutral"}}, "status": "draft"}
    ]


def test_next_reports_how_many_records_are_still_pending(
    runner: CliRunner,
    credentials: None,
    fake_client: FakeArgilla,
    fake_api: FakeAPI,
) -> None:
    """The server-side total rides on the rows, so ``-o json`` can see it too."""
    fake_api.route("POST", _search_path(fake_client), json=_a_page(total=7))

    result = runner.invoke(
        app, ["-o", "json", "annotate", "next", "reviews", "-w", "nlp-lab"]
    )

    assert result.exit_code == 0, result.output
    assert jsonlib.loads(result.stdout)[0]["pending_total"] == 7


def test_next_searches_the_me_endpoint_for_one_record_by_default(
    runner: CliRunner,
    credentials: None,
    fake_client: FakeArgilla,
    fake_api: FakeAPI,
) -> None:
    """The default is a single record: one to work on, not a page to scroll."""
    fake_api.route("POST", _search_path(fake_client), json=_a_page())

    result = runner.invoke(app, ["annotate", "next", "reviews", "-w", "nlp-lab"])

    assert result.exit_code == 0, result.output
    request = fake_api.requests[-1]
    assert request.url.path == _search_path(fake_client)
    assert request.url.params["limit"] == "1"
    assert sorted(request.url.params.get_list("include")) == [
        "responses",
        "suggestions",
    ]


def test_next_asks_for_as_many_records_as_the_limit(
    runner: CliRunner,
    credentials: None,
    fake_client: FakeArgilla,
    fake_api: FakeAPI,
) -> None:
    fake_api.route("POST", _search_path(fake_client), json=_a_page())

    result = runner.invoke(
        app, ["annotate", "next", "reviews", "-w", "nlp-lab", "--limit", "5"]
    )

    assert result.exit_code == 0, result.output
    assert fake_api.requests[-1].url.params["limit"] == "5"


def test_next_only_asks_for_records_the_caller_has_not_answered(
    runner: CliRunner,
    credentials: None,
    fake_client: FakeArgilla,
    fake_api: FakeAPI,
) -> None:
    """A queue of work means *pending*; the filter is pinned in the seam."""
    fake_api.route("POST", _search_path(fake_client), json=_a_page())

    result = runner.invoke(app, ["annotate", "next", "reviews", "-w", "nlp-lab"])

    assert result.exit_code == 0, result.output
    assert _body(fake_api.requests[-1])["filters"]["and"][0]["values"] == ["pending"]


def test_next_with_an_empty_queue_succeeds(
    runner: CliRunner,
    credentials: None,
    fake_client: FakeArgilla,
    fake_api: FakeAPI,
) -> None:
    """Nothing left to annotate is a finished job, not a failure."""
    fake_api.route("POST", _search_path(fake_client), json={"items": [], "total": 0})

    result = runner.invoke(app, ["annotate", "next", "reviews", "-w", "nlp-lab"])

    assert result.exit_code == 0, result.output
    assert "No pending records" in result.output


def test_next_with_an_empty_queue_emits_an_empty_json_list(
    runner: CliRunner,
    credentials: None,
    fake_client: FakeArgilla,
    fake_api: FakeAPI,
) -> None:
    fake_api.route("POST", _search_path(fake_client), json={"items": [], "total": 0})

    result = runner.invoke(
        app, ["-o", "json", "annotate", "next", "reviews", "-w", "nlp-lab"]
    )

    assert result.exit_code == 0, result.output
    assert jsonlib.loads(result.stdout) == []


def test_next_on_an_unknown_dataset_is_not_found(
    runner: CliRunner, credentials: None, fake_api: FakeAPI
) -> None:
    result = runner.invoke(app, ["annotate", "next", "nope", "-w", "nlp-lab"])

    assert result.exit_code == 12, result.output


def test_next_forbidden_by_the_server_is_an_auth_problem(
    runner: CliRunner,
    credentials: None,
    fake_client: FakeArgilla,
    fake_api: FakeAPI,
) -> None:
    """A key without access to the workspace gets a 403, which is exit 10."""
    fake_api.route(
        "POST", _search_path(fake_client), json={"detail": "no"}, status_code=403
    )

    result = runner.invoke(app, ["annotate", "next", "reviews", "-w", "nlp-lab"])

    assert result.exit_code == 10, result.output


def test_next_without_a_transport_is_a_network_failure(
    runner: CliRunner, credentials: None
) -> None:
    """No ``fake_api``: the client has nothing to send the request down."""
    result = runner.invoke(app, ["annotate", "next", "reviews", "-w", "nlp-lab"])

    assert result.exit_code == 11, result.output


# ---------------------------------------------------------------------------
# submit
# ---------------------------------------------------------------------------


def test_submit_sends_one_wrapped_value_per_answer(
    runner: CliRunner,
    credentials: None,
    fake_client: FakeArgilla,
    fake_api: FakeAPI,
) -> None:
    """``--answer`` is repeatable, and a JSON-parsable value is sent typed.

    ``rating=4`` has to arrive as the number 4: a rating question rejects the
    string "4".
    """
    fake_api.route("POST", _responses_path(), json={"id": "x"}, status_code=201)

    result = runner.invoke(
        app,
        [
            "annotate",
            "submit",
            "reviews",
            RECORD_ID,
            "-w",
            "nlp-lab",
            "--answer",
            "label=positive",
            "--answer",
            "rating=4",
        ],
    )

    assert result.exit_code == 0, result.output
    request = fake_api.requests[-1]
    assert request.method == "POST"
    assert request.url.path == _responses_path()
    assert _body(request) == {
        "values": {"label": {"value": "positive"}, "rating": {"value": 4}},
        "status": "submitted",
    }


def test_submit_splits_an_answer_on_the_first_equals_sign(
    runner: CliRunner,
    credentials: None,
    fake_client: FakeArgilla,
    fake_api: FakeAPI,
) -> None:
    """Free-text answers contain ``=``; only the first one is the separator."""
    fake_api.route("POST", _responses_path(), json={"id": "x"}, status_code=201)

    result = runner.invoke(
        app,
        [
            "annotate",
            "submit",
            "reviews",
            RECORD_ID,
            "-w",
            "nlp-lab",
            "--answer",
            "note=a=b",
        ],
    )

    assert result.exit_code == 0, result.output
    assert _body(fake_api.requests[-1])["values"] == {"note": {"value": "a=b"}}


def test_submit_reports_what_it_answered(
    runner: CliRunner,
    credentials: None,
    fake_client: FakeArgilla,
    fake_api: FakeAPI,
) -> None:
    fake_api.route("POST", _responses_path(), json={"id": "x"}, status_code=201)

    result = runner.invoke(
        app,
        [
            "-o",
            "json",
            "annotate",
            "submit",
            "reviews",
            RECORD_ID,
            "-w",
            "nlp-lab",
            "--answer",
            "label=positive",
        ],
    )

    assert result.exit_code == 0, result.output
    assert jsonlib.loads(result.stdout) == {
        "dataset": "reviews",
        "record_id": RECORD_ID,
        "status": "submitted",
        "questions": ["label"],
    }


def test_submit_can_leave_the_response_as_a_draft(
    runner: CliRunner,
    credentials: None,
    fake_client: FakeArgilla,
    fake_api: FakeAPI,
) -> None:
    fake_api.route("POST", _responses_path(), json={"id": "x"}, status_code=201)

    result = runner.invoke(
        app,
        [
            "annotate",
            "submit",
            "reviews",
            RECORD_ID,
            "-w",
            "nlp-lab",
            "--answer",
            "label=positive",
            "--status",
            "draft",
        ],
    )

    assert result.exit_code == 0, result.output
    assert _body(fake_api.requests[-1])["status"] == "draft"


def test_submit_rejects_a_status_the_endpoint_does_not_take(
    runner: CliRunner,
    credentials: None,
    fake_client: FakeArgilla,
    fake_api: FakeAPI,
) -> None:
    """``discarded`` has its own command; anything else is not a response
    status at all. Typer rejects the value, which is a usage error."""
    result = runner.invoke(
        app,
        [
            "annotate",
            "submit",
            "reviews",
            RECORD_ID,
            "-w",
            "nlp-lab",
            "--answer",
            "label=positive",
            "--status",
            "discarded",
        ],
    )

    assert result.exit_code == 2, result.output


def test_submit_reads_answers_from_a_json_file(
    runner: CliRunner,
    credentials: None,
    fake_client: FakeArgilla,
    fake_api: FakeAPI,
    tmp_path: Any,
) -> None:
    """The way to answer several questions, including structured values."""
    fake_api.route("POST", _responses_path(), json={"id": "x"}, status_code=201)
    source = tmp_path / "answers.json"
    source.write_text(
        jsonlib.dumps({"label": "positive", "topics": ["a", "b"]}), encoding="utf-8"
    )

    result = runner.invoke(
        app,
        [
            "annotate",
            "submit",
            "reviews",
            RECORD_ID,
            "-w",
            "nlp-lab",
            "--from",
            str(source),
        ],
    )

    assert result.exit_code == 0, result.output
    assert _body(fake_api.requests[-1])["values"] == {
        "label": {"value": "positive"},
        "topics": {"value": ["a", "b"]},
    }


def test_submit_reads_answers_from_stdin(
    runner: CliRunner,
    credentials: None,
    fake_client: FakeArgilla,
    fake_api: FakeAPI,
) -> None:
    """``--from -`` so an agent can pipe a response without a temp file."""
    fake_api.route("POST", _responses_path(), json={"id": "x"}, status_code=201)

    result = runner.invoke(
        app,
        ["annotate", "submit", "reviews", RECORD_ID, "-w", "nlp-lab", "--from", "-"],
        input='{"label": "negative"}',
    )

    assert result.exit_code == 0, result.output
    assert _body(fake_api.requests[-1])["values"] == {"label": {"value": "negative"}}


def test_submit_with_both_answer_sources_is_a_validation_error(
    runner: CliRunner,
    credentials: None,
    fake_client: FakeArgilla,
    fake_api: FakeAPI,
    tmp_path: Any,
) -> None:
    """Merging them would make the precedence invisible, so it is refused."""
    source = tmp_path / "answers.json"
    source.write_text('{"label": "positive"}', encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "annotate",
            "submit",
            "reviews",
            RECORD_ID,
            "-w",
            "nlp-lab",
            "--answer",
            "label=positive",
            "--from",
            str(source),
        ],
    )

    assert result.exit_code == 13, result.output
    assert not fake_api.requests


def test_submit_without_any_answer_is_a_validation_error(
    runner: CliRunner,
    credentials: None,
    fake_client: FakeArgilla,
    fake_api: FakeAPI,
) -> None:
    """An empty submitted response would be rejected server-side as a 422."""
    result = runner.invoke(
        app, ["annotate", "submit", "reviews", RECORD_ID, "-w", "nlp-lab"]
    )

    assert result.exit_code == 13, result.output
    assert not fake_api.requests


def test_submit_rejects_an_answer_without_an_equals_sign(
    runner: CliRunner,
    credentials: None,
    fake_client: FakeArgilla,
    fake_api: FakeAPI,
) -> None:
    result = runner.invoke(
        app,
        [
            "annotate",
            "submit",
            "reviews",
            RECORD_ID,
            "-w",
            "nlp-lab",
            "--answer",
            "label",
        ],
    )

    assert result.exit_code == 13, result.output
    assert not fake_api.requests


def test_submit_rejects_an_answer_file_that_is_not_an_object(
    runner: CliRunner,
    credentials: None,
    fake_client: FakeArgilla,
    fake_api: FakeAPI,
    tmp_path: Any,
) -> None:
    source = tmp_path / "answers.json"
    source.write_text('["label"]', encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "annotate",
            "submit",
            "reviews",
            RECORD_ID,
            "-w",
            "nlp-lab",
            "--from",
            str(source),
        ],
    )

    assert result.exit_code == 13, result.output


def test_submit_rejects_an_answer_file_that_is_not_valid_json(
    runner: CliRunner,
    credentials: None,
    fake_client: FakeArgilla,
    fake_api: FakeAPI,
    tmp_path: Any,
) -> None:
    source = tmp_path / "answers.json"
    source.write_text("label: positive", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "annotate",
            "submit",
            "reviews",
            RECORD_ID,
            "-w",
            "nlp-lab",
            "--from",
            str(source),
        ],
    )

    assert result.exit_code == 13, result.output
    assert not fake_api.requests


def test_submit_rejects_an_empty_answer_object(
    runner: CliRunner,
    credentials: None,
    fake_client: FakeArgilla,
    fake_api: FakeAPI,
    tmp_path: Any,
) -> None:
    """An answerless response is a 422 server-side, so it stops here."""
    source = tmp_path / "answers.json"
    source.write_text("{}", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "annotate",
            "submit",
            "reviews",
            RECORD_ID,
            "-w",
            "nlp-lab",
            "--from",
            str(source),
        ],
    )

    assert result.exit_code == 13, result.output
    assert not fake_api.requests


def test_submit_rejects_an_answer_file_that_is_not_utf8(
    runner: CliRunner,
    credentials: None,
    fake_client: FakeArgilla,
    fake_api: FakeAPI,
    tmp_path: Any,
) -> None:
    """``UnicodeDecodeError`` is a ValueError in builtins, so it slips past
    ``map_exception``; reading through ``read_text_file`` keeps it at 13."""
    source = tmp_path / "answers.json"
    source.write_bytes(b'{"label": "\xff\xfe"}')

    result = runner.invoke(
        app,
        [
            "annotate",
            "submit",
            "reviews",
            RECORD_ID,
            "-w",
            "nlp-lab",
            "--from",
            str(source),
        ],
    )

    assert result.exit_code == 13, result.output


def test_submit_on_an_unknown_dataset_is_not_found(
    runner: CliRunner, credentials: None, fake_api: FakeAPI
) -> None:
    """The dataset is resolved even though the endpoint takes only a record
    id, so a typo stops here rather than answering a record elsewhere."""
    result = runner.invoke(
        app,
        [
            "annotate",
            "submit",
            "nope",
            RECORD_ID,
            "-w",
            "nlp-lab",
            "--answer",
            "label=positive",
        ],
    )

    assert result.exit_code == 12, result.output
    assert not fake_api.requests


def test_submit_to_a_missing_record_is_not_found(
    runner: CliRunner,
    credentials: None,
    fake_client: FakeArgilla,
    fake_api: FakeAPI,
) -> None:
    fake_api.route("POST", _responses_path(), json={"detail": "gone"}, status_code=404)

    result = runner.invoke(
        app,
        [
            "annotate",
            "submit",
            "reviews",
            RECORD_ID,
            "-w",
            "nlp-lab",
            "--answer",
            "label=positive",
        ],
    )

    assert result.exit_code == 12, result.output


# ---------------------------------------------------------------------------
# discard
# ---------------------------------------------------------------------------


def test_discard_answers_with_a_discarded_status_and_no_values(
    runner: CliRunner,
    credentials: None,
    fake_client: FakeArgilla,
    fake_api: FakeAPI,
) -> None:
    """Discarding is an answer without content, so ``values`` is omitted."""
    fake_api.route("POST", _responses_path(), json={"id": "x"}, status_code=201)

    result = runner.invoke(
        app, ["annotate", "discard", "reviews", RECORD_ID, "-w", "nlp-lab"]
    )

    assert result.exit_code == 0, result.output
    request = fake_api.requests[-1]
    assert request.url.path == _responses_path()
    assert _body(request) == {"status": "discarded"}


def test_discard_needs_no_confirmation(
    runner: CliRunner,
    credentials: None,
    fake_client: FakeArgilla,
    fake_api: FakeAPI,
) -> None:
    """Discarding sets the caller's own response; nothing shared is destroyed,
    so it is not gated behind ``confirm()`` -- and an agent working through a
    queue would deadlock on a prompt with no terminal to answer it."""
    fake_api.route("POST", _responses_path(), json={"id": "x"}, status_code=201)

    result = runner.invoke(
        app,
        ["-o", "json", "annotate", "discard", "reviews", RECORD_ID, "-w", "nlp-lab"],
        input="",
    )

    assert result.exit_code == 0, result.output
    assert jsonlib.loads(result.stdout) == {
        "dataset": "reviews",
        "record_id": RECORD_ID,
        "status": "discarded",
    }


def test_discard_on_an_unknown_dataset_is_not_found(
    runner: CliRunner, credentials: None, fake_api: FakeAPI
) -> None:
    result = runner.invoke(app, ["annotate", "discard", "nope", RECORD_ID])

    assert result.exit_code == 12, result.output
    assert not fake_api.requests


# ---------------------------------------------------------------------------
# workspace resolution
# ---------------------------------------------------------------------------


def test_the_global_workspace_flag_disambiguates_a_repeated_name(
    runner: CliRunner,
    credentials: None,
    fake_client: FakeArgilla,
    fake_api: FakeAPI,
) -> None:
    """`reviews` exists in two workspaces; the root -w picks one, exactly as
    it does for every dataset command."""
    fake_api.route("POST", _search_path(fake_client), json=_a_page())

    result = runner.invoke(app, ["-w", "nlp-lab", "annotate", "next", "reviews"])

    assert result.exit_code == 0, result.output
    assert fake_api.requests[-1].url.path == _search_path(fake_client)


def test_an_ambiguous_dataset_name_is_a_validation_error(
    runner: CliRunner, credentials: None, fake_api: FakeAPI
) -> None:
    result = runner.invoke(app, ["annotate", "next", "reviews"])

    assert result.exit_code == 13, result.output
