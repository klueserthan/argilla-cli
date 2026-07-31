"""Tests for the `user` and `server` command groups."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from argilla_cli.main import app

from .conftest import FakeArgilla, FakeUser


def test_me_table_and_json(runner: CliRunner, credentials: None) -> None:
    """`user me` reports the fake 'owner' user in both table and json form."""
    table = runner.invoke(app, ["user", "me"])
    assert table.exit_code == 0, table.output
    assert "owner" in table.output

    result = runner.invoke(app, ["-o", "json", "user", "me"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["username"] == "owner"
    assert payload["role"] == "owner"


def test_list_shows_both_users(runner: CliRunner, credentials: None) -> None:
    """`user list` returns alice and bob."""
    result = runner.invoke(app, ["-o", "json", "user", "list"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    usernames = {row["username"] for row in payload}
    assert usernames == {"alice", "bob"}


def test_list_filtered_by_workspace_shows_only_alice(
    runner: CliRunner, credentials: None
) -> None:
    """`user list --workspace nlp-lab` returns only alice (bob is not a member)."""
    result = runner.invoke(app, ["-o", "json", "user", "list", "-w", "nlp-lab"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    usernames = {row["username"] for row in payload}
    assert usernames == {"alice"}


def test_show_alice_includes_annotator_role(
    runner: CliRunner, credentials: None
) -> None:
    """`user show alice` reports her role as annotator."""
    result = runner.invoke(app, ["-o", "json", "user", "show", "alice"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["role"] == "annotator"


def test_show_missing_user_is_not_found(runner: CliRunner, credentials: None) -> None:
    """`user show` on an unknown username exits 12."""
    result = runner.invoke(app, ["user", "show", "ghost"])
    assert result.exit_code == 12, result.output


def test_create_user_via_patched_sdk(
    runner: CliRunner,
    credentials: None,
    monkeypatch: pytest.MonkeyPatch,
    fake_client: FakeArgilla,
) -> None:
    """`user create` builds a user via the lazily-imported SDK's User class."""
    import argilla

    def _factory(
        *,
        username: str,
        password: str,
        role: str,
        first_name: str | None,
        last_name: str | None,
        client: object,
    ) -> object:
        user = FakeUser(username, role=role)
        fake_client._users.append(user)

        class _Creator:
            def create(self) -> FakeUser:
                return user

        return _Creator()

    monkeypatch.setattr(argilla, "User", _factory)

    result = runner.invoke(app, ["user", "create", "carol", "--password", "s3cret-pw"])

    assert result.exit_code == 0, result.output
    assert "carol" in result.output


def test_delete_user_with_yes_succeeds(
    runner: CliRunner, credentials: None, fake_client: FakeArgilla
) -> None:
    """`user delete -y` removes the user from the server and exits 0."""
    alice = next(u for u in fake_client._users if u.username == "alice")

    result = runner.invoke(app, ["-y", "user", "delete", "alice"])

    assert result.exit_code == 0, result.output
    assert alice.deleted is True
    assert "alice" not in [u.username for u in fake_client._users]


def test_add_to_workspace_then_verify(runner: CliRunner, credentials: None) -> None:
    """`user add-to-workspace` adds bob to archive; `workspace users` shows it."""
    result = runner.invoke(app, ["user", "add-to-workspace", "bob", "archive"])
    assert result.exit_code == 0, result.output

    check = runner.invoke(app, ["-o", "json", "workspace", "users", "archive"])
    payload = json.loads(check.stdout)
    usernames = {row["username"] for row in payload}
    assert usernames == {"bob"}


def test_remove_from_workspace_with_yes(runner: CliRunner, credentials: None) -> None:
    """`user remove-from-workspace -y` removes alice from nlp-lab."""
    result = runner.invoke(
        app, ["-y", "user", "remove-from-workspace", "alice", "nlp-lab"]
    )
    assert result.exit_code == 0, result.output

    check = runner.invoke(app, ["-o", "json", "workspace", "users", "nlp-lab"])
    payload = json.loads(check.stdout)
    assert payload == []


def test_server_health_ok_with_credentials(
    runner: CliRunner, credentials: None
) -> None:
    """`server health` exits 0 when the fake client is reachable."""
    result = runner.invoke(app, ["server", "health"])
    assert result.exit_code == 0, result.output


def test_server_whoami_prints_owner(runner: CliRunner, credentials: None) -> None:
    """`server whoami` reports the fake 'owner' user."""
    result = runner.invoke(app, ["-o", "json", "server", "whoami"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["username"] == "owner"
    assert payload["role"] == "owner"


def test_server_info_without_http_client_is_network_error(
    runner: CliRunner, credentials: None
) -> None:
    """`server info` exits 11 (no traceback) when the client lacks http_client.

    FakeArgilla has no ``http_client`` attribute, so
    ``clients.argilla_client.server_info`` raises ``NetworkApiError``.
    """
    result = runner.invoke(app, ["server", "info"])

    assert result.exit_code == 11, result.output
    assert "Traceback" not in result.output
