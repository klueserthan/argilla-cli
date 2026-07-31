"""Per-invocation session state: which server, which profile, which client.

Presentation state (output format, verbosity) lives in :mod:`argilla_cli.io_utils`
instead, so that the error layer can print without importing this module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from argilla_cli.clients import argilla_client
from argilla_cli.settings import (
    Settings,
    SettingsInfo,
    load_settings,
    require_credentials,
)


@dataclass
class CLIContext:
    """Connection-facing state shared by every command.

    Settings and the SDK client are built lazily and cached, so commands that
    never touch the network (``--help``, ``config show``) never pay for them.
    """

    profile: str | None = None
    api_url: str | None = None
    api_key: str | None = None
    assume_yes: bool = False
    default_workspace: str | None = None

    _info: SettingsInfo | None = field(default=None, repr=False, compare=False)
    _client: Any = field(default=None, repr=False, compare=False)

    def settings_info(self) -> SettingsInfo:
        """Resolve settings once per invocation."""
        if self._info is None:
            self._info = load_settings(
                profile=self.profile,
                api_url=self.api_url,
                api_key=self.api_key,
            )
        return self._info

    @property
    def settings(self) -> Settings:
        return self.settings_info().settings

    def require_settings(self) -> Settings:
        """Settings guaranteed to carry credentials, or ``AuthConfigError``."""
        return require_credentials(self.settings_info())

    def client(self) -> Any:
        """Build (and cache) an Argilla client for this invocation.

        Looked up through the module rather than imported by name so tests can
        monkeypatch ``argilla_client.get_client`` in one place.
        """
        if self._client is None:
            self._client = argilla_client.get_client(self.require_settings())
        return self._client

    def reset(self) -> None:
        """Clear all state. Used between test invocations."""
        self.profile = None
        self.api_url = None
        self.api_key = None
        self.assume_yes = False
        self.default_workspace = None
        self._info = None
        self._client = None


#: Module-level singleton populated by the root callback in ``main``.
ctx = CLIContext()
