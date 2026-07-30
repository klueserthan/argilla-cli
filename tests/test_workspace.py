"""Tests for the `workspace` command group."""

from __future__ import annotations

import json

import pytest
import yaml
from typer.testing import CliRunner

from argilla_cli.main import app

from .conftest import FakeArgilla, FakeWorkspace


def test_list_table_shows_both_workspaces(runner: CliRunner, credentials: None) -> None:
    """`workspace list` renders a table row per workspace."""
    result = runner.invoke(app, ["workspace", "list"])

    assert result.exit_code == 0, result.output
    assert "nlp-lab" in result.output
    assert "archive" in result.output


def test_list_json_has_expected_keys_and_names(
    runner: CliRunner, credentials: None
) -> None:
    """`workspace list -o json` returns rows with name/id/created_at/description."""
    result = runner.invoke(app, ["-o", "json", "workspace", "list"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    names = {row["name"] for row in payload}
    assert names == {"nlp-lab", "archive"}
    for row in payload:
        assert set(row.keys()) == {"name", "id", "created_at", "description"}


def test_list_yaml_parses(runner: CliRunner, credentials: None) -> None:
    """`workspace list -o yaml` is valid YAML with both workspace names."""
    result = runner.invoke(app, ["-o", "yaml", "workspace", "list"])

    assert result.exit_code == 0, result.output
    payload = yaml.safe_load(result.stdout)
    names = {row["name"] for row in payload}
    assert names == {"nlp-lab", "archive"}


def test_show_includes_datasets_and_users(runner: CliRunner, credentials: None) -> None:
    """`workspace show` lists the workspace's datasets and member users."""
    result = runner.invoke(app, ["-o", "json", "workspace", "show", "nlp-lab"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["name"] == "nlp-lab"
    assert set(payload["datasets"]) == {"reviews", "intents"}
    assert payload["users"] == ["alice"]


def test_show_missing_workspace_is_not_found(
    runner: CliRunner, credentials: None
) -> None:
    """`workspace show` on an unknown workspace exits 12."""
    result = runner.invoke(app, ["workspace", "show", "ghost"])
    assert result.exit_code == 12, result.output


def test_create_new_workspace_succeeds(
    runner: CliRunner,
    credentials: None,
    monkeypatch: pytest.MonkeyPatch,
    fake_client: FakeArgilla,
) -> None:
    """`workspace create` builds a workspace via the lazily-imported SDK."""
    import argilla

    def _factory(*, name: str, client: object) -> FakeWorkspace:
        workspace = FakeWorkspace(name)
        workspace._client = fake_client
        fake_client._workspaces.append(workspace)

        class _Creator:
            def create(self) -> FakeWorkspace:
                return workspace

        return _Creator()  # type: ignore[return-value]

    monkeypatch.setattr(argilla, "Workspace", _factory)

    result = runner.invoke(app, ["workspace", "create", "brand-new"])

    assert result.exit_code == 0, result.output
    assert "brand-new" in result.output


def test_delete_with_yes_removes_workspace(
    runner: CliRunner, credentials: None, fake_client: FakeArgilla
) -> None:
    """`workspace delete -y` deletes without prompting."""
    result = runner.invoke(app, ["-y", "workspace", "delete", "nlp-lab"])

    assert result.exit_code == 0, result.output
    assert "nlp-lab" not in [ws.name for ws in fake_client._workspaces]


def test_delete_without_yes_aborts_on_no(
    runner: CliRunner, credentials: None, fake_client: FakeArgilla
) -> None:
    """Declining the confirmation prompt leaves the workspace untouched."""
    result = runner.invoke(app, ["workspace", "delete", "nlp-lab"], input="n\n")

    assert result.exit_code != 0
    assert "nlp-lab" in [ws.name for ws in fake_client._workspaces]


def test_users_lists_members(runner: CliRunner, credentials: None) -> None:
    """`workspace users` lists alice as a member of nlp-lab."""
    result = runner.invoke(app, ["-o", "json", "workspace", "users", "nlp-lab"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    usernames = {row["username"] for row in payload}
    assert usernames == {"alice"}


def test_add_user_adds_bob_to_workspace(runner: CliRunner, credentials: None) -> None:
    """`workspace add-user` adds bob, then `workspace users` shows him."""
    add = runner.invoke(app, ["workspace", "add-user", "nlp-lab", "bob"])
    assert add.exit_code == 0, add.output

    result = runner.invoke(app, ["-o", "json", "workspace", "users", "nlp-lab"])
    payload = json.loads(result.stdout)
    usernames = {row["username"] for row in payload}
    assert usernames == {"alice", "bob"}


def test_add_user_missing_user_is_not_found(
    runner: CliRunner, credentials: None
) -> None:
    """`workspace add-user` with an unknown username exits 12."""
    result = runner.invoke(app, ["workspace", "add-user", "nlp-lab", "ghost"])
    assert result.exit_code == 12, result.output


def test_remove_user_with_yes_removes_alice(
    runner: CliRunner, credentials: None
) -> None:
    """`workspace remove-user -y` removes alice from nlp-lab."""
    result = runner.invoke(app, ["-y", "workspace", "remove-user", "nlp-lab", "alice"])

    assert result.exit_code == 0, result.output
    users = runner.invoke(app, ["-o", "json", "workspace", "users", "nlp-lab"])
    payload = json.loads(users.stdout)
    assert payload == []
