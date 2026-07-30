"""Lookup helpers for Argilla resources.

The SDK's accessors return ``None`` for a missing resource rather than
raising, which is easy to forget: the previous ``workspace delete`` called
``.delete()`` straight onto that ``None`` and surfaced
``'NoneType' object has no attribute 'delete'`` with a generic exit 1. Every
lookup goes through this module so a missing resource always becomes a clean
``NotFoundError`` (exit 12).
"""

from __future__ import annotations

from typing import Any

from argilla_cli.errors import NotFoundError, ValidationError


def _name_of(obj: Any) -> str:
    return str(getattr(obj, "name", "") or "")


def workspace_name_of(dataset: Any) -> str:
    """Best-effort workspace name for a dataset object."""
    workspace = getattr(dataset, "workspace", None)
    if workspace is None:
        return ""
    if isinstance(workspace, str):
        return workspace
    return _name_of(workspace)


def list_workspaces(client: Any) -> list[Any]:
    return list(client.workspaces)


def resolve_workspace(client: Any, name: str) -> Any:
    """Fetch a workspace by name or raise ``NotFoundError``."""
    workspace = client.workspaces(name)
    if workspace is None:
        raise NotFoundError(f"workspace {name!r} not found")
    return workspace


def list_datasets(client: Any, workspace: str | None = None) -> list[Any]:
    """List datasets, optionally filtered to one workspace.

    Uses the single ``datasets.list()`` call instead of iterating workspaces
    and issuing one API request per workspace.
    """
    datasets = list(client.datasets)
    if workspace is None:
        return datasets
    resolve_workspace(client, workspace)  # validate, so a typo isn't "empty"
    return [ds for ds in datasets if workspace_name_of(ds) == workspace]


def resolve_dataset(client: Any, name: str, workspace: str | None = None) -> Any:
    """Fetch a dataset by name, disambiguating across workspaces if needed."""
    if workspace:
        dataset = client.datasets(name, workspace=workspace)
        if dataset is None:
            raise NotFoundError(
                f"dataset {name!r} not found in workspace {workspace!r}"
            )
        return dataset

    matches = [ds for ds in client.datasets if _name_of(ds) == name]
    if not matches:
        raise NotFoundError(f"dataset {name!r} not found")
    if len(matches) > 1:
        found = ", ".join(sorted(workspace_name_of(ds) for ds in matches))
        raise ValidationError(
            f"dataset {name!r} exists in multiple workspaces: {found}. "
            "Use --workspace to disambiguate."
        )
    return matches[0]


def list_users(client: Any, workspace: str | None = None) -> list[Any]:
    if workspace:
        return list(resolve_workspace(client, workspace).users)
    return list(client.users)


def resolve_user(client: Any, username: str) -> Any:
    """Fetch a user by username or raise ``NotFoundError``."""
    user = client.users(username)
    if user is None:
        raise NotFoundError(f"user {username!r} not found")
    return user
