from __future__ import annotations

import os
from unittest import mock

from argilla_cli.settings import load_settings


def test_load_settings_env_precedence(monkeypatch):
    monkeypatch.setenv("ARGILLA_API_URL", "https://example.com")
    monkeypatch.setenv("ARGILLA_API_KEY", "rbga_test")
    info = load_settings()
    api_url = str(info.settings.argilla_api_url).rstrip("/")
    assert api_url == "https://example.com"
    assert info.settings.argilla_api_key == "rbga_test"
    assert info.sources["ARGILLA_API_URL"] == "env"
    assert info.sources["ARGILLA_API_KEY"] == "env"
