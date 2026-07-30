"""Regression tests for the defects found when reviving the CLI.

Each test names the behaviour that was broken. Most of these are exit-code
assertions, because the exit codes were exactly what the old code got wrong:
deliberate ``typer.Exit`` calls were swallowed by ``except Exception`` blocks
(``typer.Exit`` subclasses ``RuntimeError``), and the error classifier matched
message substrings.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from argilla_cli.errors import (
    AuthConfigError,
    NetworkApiError,
    NotFoundError,
    ValidationError,
    map_exception,
)
from argilla_cli.main import app

from .conftest import FakeArgilla, FakeWorkspace


def test_b1_workspace_create_exists_ok_succeeds(
    runner: CliRunner, credentials: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--exists-ok must exit 0, not print success and then fail.

    Previously ``raise typer.Exit(code=0)`` was caught by an enclosing
    ``except Exception: pass``, so the command fell through to creating the
    workspace anyway and died with exit 1 after already reporting success.
    """
    import argilla

    monkeypatch.setattr(argilla, "Workspace", _unexpected_workspace_factory)

    result = runner.invoke(app, ["workspace", "create", "nlp-lab", "--exists-ok"])

    assert result.exit_code == 0, result.output
    assert "already exists" in result.output.lower()


def test_b1_workspace_create_without_exists_ok_is_validation_error(
    runner: CliRunner, credentials: None
) -> None:
    """Creating a duplicate without --exists-ok is a validation error (13)."""
    result = runner.invoke(app, ["workspace", "create", "nlp-lab"])
    assert result.exit_code == 13, result.output


def test_b2_unsupported_format_is_usage_error(
    runner: CliRunner, credentials: None
) -> None:
    """A bad --fmt is a usage error (2), not a network error (11).

    The old download body wrapped everything in ``except Exception`` and
    rewrote every failure -- including its own ``typer.Exit(2)`` -- to 11.
    """
    result = runner.invoke(
        app, ["dataset", "download", "reviews", "-w", "nlp-lab", "--fmt", "bogus"]
    )
    assert result.exit_code == 2, result.output


def test_b2_existing_output_is_validation_error(
    runner: CliRunner, credentials: None, tmp_path: Path
) -> None:
    """Refusing to clobber an existing file exits 13, not 11."""
    target = Path("reviews.jsonl")
    target.write_text("existing", encoding="utf-8")

    result = runner.invoke(
        app, ["dataset", "download", "reviews", "-w", "nlp-lab", "-O", str(target)]
    )
    assert result.exit_code == 13, result.output
    assert target.read_text(encoding="utf-8") == "existing"


@pytest.mark.parametrize(
    "message",
    ["dataset ds5 is missing", "record 5 failed", "id 550e8400-e29b-41d4"],
)
def test_b3_digit_five_is_not_a_network_error(message: str) -> None:
    """A message containing '5' must not be classified as a network failure.

    ``map_exception`` used to test ``"5" in message.lower()``, so any error
    mentioning a 5 -- a UUID, a dataset named ds5 -- became exit 11.
    """
    mapped = map_exception(RuntimeError(message))
    assert not isinstance(mapped, NetworkApiError)
    assert mapped.exit_code == 1


def test_b3_status_codes_drive_classification() -> None:
    """Classification comes from the status code, not the message text."""

    class ApiError(Exception):
        def __init__(self, message: str, status_code: int) -> None:
            super().__init__(message)
            self.status_code = status_code

    assert isinstance(map_exception(ApiError("nope", 401)), AuthConfigError)
    assert isinstance(map_exception(ApiError("nope", 404)), NotFoundError)
    assert isinstance(map_exception(ApiError("nope", 409)), ValidationError)
    assert isinstance(map_exception(ApiError("nope", 503)), NetworkApiError)


def test_b4_deleting_missing_workspace_is_not_found(
    runner: CliRunner, credentials: None
) -> None:
    """A missing workspace exits 12 with a real message.

    ``client.workspaces(name)`` returns ``None`` rather than raising, so the
    old code called ``.delete()`` on ``None`` and surfaced
    ``'NoneType' object has no attribute 'delete'`` with exit 1.
    """
    result = runner.invoke(app, ["-y", "workspace", "delete", "ghost"])

    assert result.exit_code == 12, result.output
    assert "NoneType" not in result.output
    assert "ghost" in result.output


def test_b5_config_show_works_without_credentials(runner: CliRunner) -> None:
    """`config show` must render when nothing is configured -- that is its job.

    It used to raise a raw pydantic ValidationError traceback.
    """
    result = runner.invoke(app, ["config", "show"])

    assert result.exit_code == 0, result.output
    assert "Traceback" not in result.output
    assert "unset" in result.output


def test_b5_commands_needing_credentials_exit_10(runner: CliRunner) -> None:
    """Commands that must reach the server report a clean auth error."""
    result = runner.invoke(app, ["workspace", "list"])

    assert result.exit_code == 10, result.output
    assert "Traceback" not in result.output
    assert "ARGILLA_API_URL" in result.output


def test_b9_user_me_reports_errors_cleanly(
    runner: CliRunner, credentials: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`user me` no longer dumps a traceback on failure."""
    from argilla_cli.clients import argilla_client

    class Boom(FakeArgilla):
        @property
        def me(self):  # type: ignore[override]
            raise RuntimeError("boom")

        @me.setter
        def me(self, value):  # type: ignore[no-untyped-def]
            pass

    monkeypatch.setattr(argilla_client, "get_client", lambda settings: Boom())

    result = runner.invoke(app, ["user", "me"])

    assert result.exit_code != 0
    assert "Traceback" not in result.output


def test_b9_user_me_honours_global_output_flag(
    runner: CliRunner, credentials: None
) -> None:
    """`user me` respects the global -o json (it ignored --json before)."""
    result = runner.invoke(app, ["-o", "json", "user", "me"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["username"] == "owner"


def test_b15_table_columns_are_not_alphabetical(
    runner: CliRunner, credentials: None
) -> None:
    """Tables lead with the identifying column instead of sorting keys."""
    result = runner.invoke(app, ["-o", "csv", "workspace", "list"])

    assert result.exit_code == 0, result.output
    header = result.stdout.strip().splitlines()[0]
    assert header.startswith("name,id")


def test_b18_version_flag(runner: CliRunner) -> None:
    """--version reports the installed version."""
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "argilla-cli" in result.stdout


def _unexpected_workspace_factory(*args: object, **kwargs: object) -> FakeWorkspace:
    raise AssertionError(
        "workspace creation must not be attempted when it already exists"
    )
