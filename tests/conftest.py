"""Shared fixtures: an isolated environment and a fake Argilla server.

Every test runs against ``FakeArgilla`` rather than a live server. The fake
deliberately mirrors two SDK behaviours that the CLI has to get right:

* accessors are both iterable and callable (``client.workspaces`` /
  ``client.workspaces("name")``), and
* a callable accessor returns ``None`` for a missing resource instead of
  raising -- the source of the old ``'NoneType' object has no attribute
  'delete'`` crash.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from argilla_cli import io_utils
from argilla_cli.context import ctx

ENV_VARS = (
    "ARGILLA_API_URL",
    "ARGILLA_API_KEY",
    "HF_TOKEN",
    "ARGILLA_DEFAULT_OUTPUT_DIR",
    "ARGILLA_CLI_CONFIG",
    "ARGILLA_CLI_PROFILE",
    "XDG_CONFIG_HOME",
)


@pytest.fixture(autouse=True)
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Cut every test off from the developer's real environment.

    Without this, an ambient ``ARGILLA_API_KEY``, a stray ``.env`` in the
    repo root, or a real ``~/.config/argilla-cli/config.toml`` would leak in
    and make results depend on the machine running them.
    """
    for var in ENV_VARS:
        monkeypatch.delenv(var, raising=False)

    workdir = tmp_path / "cwd"
    workdir.mkdir()
    monkeypatch.chdir(workdir)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("ARGILLA_CLI_CONFIG", str(tmp_path / "config" / "config.toml"))

    io_utils.reset()
    ctx.reset()
    yield
    io_utils.reset()
    ctx.reset()


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provide valid-looking credentials via the environment."""
    monkeypatch.setenv("ARGILLA_API_URL", "https://argilla.example.com")
    monkeypatch.setenv("ARGILLA_API_KEY", "rbga_test_key")


# ---------------------------------------------------------------------------
# Fake Argilla SDK
# ---------------------------------------------------------------------------


def _uuid(seed: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, seed))


class FakeRecords:
    """Stand-in for ``dataset.records``."""

    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = rows or []
        self.logged: list[dict[str, Any]] = []

    def __iter__(self) -> Iterator[dict[str, Any]]:
        return iter(self.rows)

    def to_list(self, flatten: bool = False) -> list[dict[str, Any]]:
        if not flatten:
            return [dict(row) for row in self.rows]
        flat: list[dict[str, Any]] = []
        for row in self.rows:
            item: dict[str, Any] = {}
            for key, value in row.items():
                if isinstance(value, dict):
                    item.update({f"{key}.{k}": v for k, v in value.items()})
                else:
                    item[key] = value
            flat.append(item)
        return flat

    def log(self, records: list[dict[str, Any]]) -> None:
        self.logged.extend(records)
        self.rows.extend(records)


class FakeSettings:
    def __init__(self) -> None:
        self.guidelines = "Annotate carefully."
        self.fields = [type("F", (), {"name": "text"})()]
        self.questions = [type("Q", (), {"name": "label"})()]
        self.metadata: list[Any] = []
        self.vectors: list[Any] = []
        self.exported_to: Path | None = None

    def serialize(self) -> dict[str, Any]:
        return {
            "guidelines": self.guidelines,
            "fields": [{"name": "text"}],
            "questions": [{"name": "label"}],
        }

    def to_json(self, path: Path | str) -> None:
        import json

        self.exported_to = Path(path)
        Path(path).write_text(json.dumps(self.serialize()), encoding="utf-8")


class FakeDataset:
    def __init__(
        self,
        name: str,
        workspace: str,
        records: list[dict[str, Any]] | None = None,
    ) -> None:
        self.name = name
        self.workspace = workspace
        self.id = _uuid(f"dataset/{workspace}/{name}")
        self.inserted_at = "2025-01-01T00:00:00Z"
        self.description = f"{name} description"
        self.records = FakeRecords(records)
        self.settings = FakeSettings()
        self.deleted = False
        self._progress = {"total": 2, "completed": 1, "pending": 1}

    def delete(self) -> FakeDataset:
        self.deleted = True
        return self

    def progress(self, with_users_distribution: bool = False) -> dict[str, Any]:
        data = dict(self._progress)
        if with_users_distribution:
            data["users"] = {
                "alice": {
                    "completed": {"submitted": 1, "draft": 0, "discarded": 0},
                    "pending": {"submitted": 0, "draft": 1, "discarded": 0},
                }
            }
        return data


class FakeUser:
    def __init__(self, username: str, role: str = "annotator") -> None:
        self.username = username
        self.role = role
        self.id = _uuid(f"user/{username}")
        self.first_name = username.capitalize()
        self.last_name = "Example"
        self.workspaces: list[FakeWorkspace] = []
        self.deleted = False
        self._client: FakeArgilla | None = None

    def delete(self) -> FakeUser:
        self.deleted = True
        if self._client is not None and self in self._client._users:
            self._client._users.remove(self)
        return self

    def add_to_workspace(self, workspace: FakeWorkspace) -> FakeUser:
        if workspace not in self.workspaces:
            self.workspaces.append(workspace)
        if self not in workspace.users:
            workspace.users.append(self)
        return self

    def remove_from_workspace(self, workspace: FakeWorkspace) -> FakeUser:
        if workspace in self.workspaces:
            self.workspaces.remove(workspace)
        if self in workspace.users:
            workspace.users.remove(self)
        return self


class FakeWorkspace:
    def __init__(self, name: str) -> None:
        self.name = name
        self.id = _uuid(f"workspace/{name}")
        self.created_at = "2025-01-01T00:00:00Z"
        self.description = f"{name} workspace"
        self.users: list[FakeUser] = []
        self.deleted = False
        self._client: FakeArgilla | None = None

    @property
    def datasets(self) -> list[FakeDataset]:
        if self._client is None:
            return []
        return [ds for ds in self._client._datasets if ds.workspace == self.name]

    def delete(self) -> FakeWorkspace:
        self.deleted = True
        if self._client is not None and self in self._client._workspaces:
            self._client._workspaces.remove(self)
        return self

    def add_user(self, user: FakeUser) -> FakeWorkspace:
        user.add_to_workspace(self)
        return self

    def remove_user(self, user: FakeUser) -> FakeWorkspace:
        user.remove_from_workspace(self)
        return self


class _Accessor:
    """Iterable + callable accessor, mirroring the real SDK's collections."""

    def __init__(self, items: list[Any], key: str) -> None:
        self._items = items
        self._key = key

    def __iter__(self) -> Iterator[Any]:
        return iter(list(self._items))

    def __len__(self) -> int:
        return len(self._items)

    def list(self) -> list[Any]:
        return list(self._items)

    def __call__(self, name: str | None = None, workspace: Any = None) -> Any:
        """Return the matching item, or ``None`` when absent (never raises)."""
        matches = [i for i in self._items if getattr(i, self._key, None) == name]
        if workspace is not None:
            ws_name = getattr(workspace, "name", workspace)
            matches = [i for i in matches if getattr(i, "workspace", None) == ws_name]
        return matches[0] if matches else None


class FakeArgilla:
    """Minimal stand-in for ``rg.Argilla``."""

    def __init__(
        self,
        workspaces: list[FakeWorkspace] | None = None,
        datasets: list[FakeDataset] | None = None,
        users: list[FakeUser] | None = None,
        me: FakeUser | None = None,
    ) -> None:
        self._workspaces = workspaces if workspaces is not None else []
        self._datasets = datasets if datasets is not None else []
        self._users = users if users is not None else []
        for workspace in self._workspaces:
            workspace._client = self
        for user in self._users:
            user._client = self
        self.me = me or FakeUser("owner", role="owner")

    @property
    def workspaces(self) -> _Accessor:
        return _Accessor(self._workspaces, "name")

    @property
    def datasets(self) -> _Accessor:
        return _Accessor(self._datasets, "name")

    @property
    def users(self) -> _Accessor:
        return _Accessor(self._users, "username")


@pytest.fixture
def fake_client() -> FakeArgilla:
    """A populated fake server: two workspaces, three datasets, two users."""
    ws_main = FakeWorkspace("nlp-lab")
    ws_other = FakeWorkspace("archive")

    alice = FakeUser("alice", role="annotator")
    bob = FakeUser("bob", role="admin")
    alice.add_to_workspace(ws_main)

    records = [
        {"id": "r1", "status": "completed", "fields": {"text": "hello"}},
        {"id": "r2", "status": "pending", "fields": {"text": "world"}},
    ]
    datasets = [
        FakeDataset("reviews", "nlp-lab", [dict(r) for r in records]),
        FakeDataset("intents", "nlp-lab", []),
        # Same name in two workspaces, to exercise the ambiguity path.
        FakeDataset("reviews", "archive", []),
    ]

    return FakeArgilla(
        workspaces=[ws_main, ws_other],
        datasets=datasets,
        users=[alice, bob],
    )


@pytest.fixture(autouse=True)
def patch_client(
    monkeypatch: pytest.MonkeyPatch, fake_client: FakeArgilla
) -> FakeArgilla:
    """Route ``ctx.client()`` to the fake server for every test."""
    from argilla_cli.clients import argilla_client

    monkeypatch.setattr(
        argilla_client, "get_client", lambda settings: fake_client
    )
    return fake_client
