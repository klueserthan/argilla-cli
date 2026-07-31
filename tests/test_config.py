"""Tests for the `config` command group: precedence, profiles, and doctor."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from argilla_cli.main import app

from .conftest import FakeArgilla


def test_show_reports_env_source_for_credentials(
    runner: CliRunner, credentials: None
) -> None:
    """`config show` names "env" as the source when only env vars are set."""
    result = runner.invoke(app, ["-o", "json", "config", "show"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["api_url_source"] == "env"
    assert payload["api_url"] == "https://argilla.example.com"


def test_set_writes_profile_file_and_show_reads_it_back(runner: CliRunner) -> None:
    """`config set` persists to config.toml and `config show` reports it."""
    result = runner.invoke(
        app, ["config", "set", "api_url", "https://argilla.example.com"]
    )
    assert result.exit_code == 0, result.output

    config_file = Path(os.environ["ARGILLA_CLI_CONFIG"])
    assert config_file.is_file()

    show = runner.invoke(app, ["-o", "json", "config", "show"])
    assert show.exit_code == 0, show.output
    payload = json.loads(show.stdout)
    assert payload["api_url"] == "https://argilla.example.com"
    assert payload["api_url_source"] == "profile:default"


def test_env_wins_over_profile_value(runner: CliRunner, credentials: None) -> None:
    """A profile value is set, but env credentials take precedence over it."""
    set_result = runner.invoke(
        app, ["config", "set", "api_url", "https://profile.example.com"]
    )
    assert set_result.exit_code == 0, set_result.output

    show = runner.invoke(app, ["-o", "json", "config", "show"])
    assert show.exit_code == 0, show.output
    payload = json.loads(show.stdout)
    assert payload["api_url_source"] == "env"
    assert payload["api_url"] == "https://argilla.example.com"


def test_flag_wins_over_env(runner: CliRunner, credentials: None) -> None:
    """A global --api-url flag beats env vars, which beat the profile."""
    result = runner.invoke(
        app,
        ["--api-url", "https://flag.example.com", "-o", "json", "config", "show"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["api_url_source"] == "flag"
    assert payload["api_url"] == "https://flag.example.com"


def test_set_unknown_key_is_validation_error(runner: CliRunner) -> None:
    """Setting an unknown key exits 13 and lists the valid keys."""
    result = runner.invoke(app, ["config", "set", "bogus", "value"])

    assert result.exit_code == 13, result.output
    for key in ("api_url", "api_key", "hf_token", "default_output_dir"):
        assert key in result.output


def test_list_profiles_empty_then_populated(runner: CliRunner) -> None:
    """`config list` shows the empty message, then both profiles after two sets."""
    empty = runner.invoke(app, ["config", "list"])
    assert empty.exit_code == 0, empty.output
    assert "no profiles configured" in empty.output.lower()

    set_a = runner.invoke(
        app,
        ["config", "set", "api_url", "https://a.example.com", "--profile", "a"],
    )
    set_b = runner.invoke(
        app,
        ["config", "set", "api_url", "https://b.example.com", "--profile", "b"],
    )
    assert set_a.exit_code == 0, set_a.output
    assert set_b.exit_code == 0, set_b.output

    listed = runner.invoke(app, ["-o", "json", "config", "list"])
    assert listed.exit_code == 0, listed.output
    payload = json.loads(listed.stdout)
    names = {row["name"] for row in payload}
    assert names == {"a", "b"}


def test_use_missing_profile_is_not_found(runner: CliRunner) -> None:
    """`config use` on a profile that doesn't exist exits 12."""
    result = runner.invoke(app, ["config", "use", "ghost"])
    assert result.exit_code == 12, result.output


def test_use_existing_profile_becomes_current(runner: CliRunner) -> None:
    """`config use` selects the profile, and `config show` then reflects it."""
    runner.invoke(
        app,
        ["config", "set", "api_url", "https://a.example.com", "--profile", "a"],
    )

    result = runner.invoke(app, ["config", "use", "a"])
    assert result.exit_code == 0, result.output

    show = runner.invoke(app, ["-o", "json", "config", "show"])
    assert show.exit_code == 0, show.output
    payload = json.loads(show.stdout)
    assert payload["profile"] == "a"
    assert payload["api_url"] == "https://a.example.com"


def test_get_returns_value_from_named_profile(runner: CliRunner) -> None:
    """`config get` reads a single key back out of a named profile."""
    runner.invoke(
        app,
        ["config", "set", "api_key", "rbga_secret", "--profile", "x"],
    )

    result = runner.invoke(
        app, ["-o", "json", "config", "get", "api_key", "--profile", "x"]
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["value"] == "rbga_secret"


def test_get_unset_key_is_validation_error(runner: CliRunner) -> None:
    """`config get` for a key that was never set in the profile exits 13."""
    runner.invoke(
        app,
        ["config", "set", "api_url", "https://a.example.com", "--profile", "x"],
    )

    result = runner.invoke(app, ["config", "get", "hf_token", "--profile", "x"])

    assert result.exit_code == 13, result.output


def test_remove_profile_with_confirmation_deletes_it(runner: CliRunner) -> None:
    """`config remove -y` deletes the profile; it disappears from `config list`."""
    runner.invoke(
        app,
        ["config", "set", "api_url", "https://a.example.com", "--profile", "a"],
    )

    result = runner.invoke(app, ["-y", "config", "remove", "a"])
    assert result.exit_code == 0, result.output

    listed = runner.invoke(app, ["-o", "json", "config", "list"])
    assert listed.exit_code == 0, listed.output
    assert json.loads(listed.stdout) == []


def test_doctor_succeeds_with_valid_credentials(
    runner: CliRunner, credentials: None, fake_client: FakeArgilla
) -> None:
    """`config doctor` exits 0 when the fake server connects fine."""
    result = runner.invoke(app, ["config", "doctor"])
    assert result.exit_code == 0, result.output


def test_doctor_rejects_malformed_hf_token(
    runner: CliRunner, credentials: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`config doctor` exits 13 when HF_TOKEN doesn't look like a real token."""
    monkeypatch.setenv("HF_TOKEN", "garbage")

    result = runner.invoke(app, ["config", "doctor"])

    assert result.exit_code == 13, result.output


def test_show_json_never_leaks_the_raw_api_key(
    runner: CliRunner, credentials: None
) -> None:
    """`config show -o json` masks the api_key; the raw secret must not leak."""
    result = runner.invoke(app, ["-o", "json", "config", "show"])

    assert result.exit_code == 0, result.output
    assert "rbga_test_key" not in result.stdout
