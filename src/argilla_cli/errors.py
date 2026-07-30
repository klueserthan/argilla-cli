"""Error taxonomy, exception mapping, and the shared command error handler.

Exit codes are part of the CLI's contract:

===== =========================================================
Code  Meaning
===== =========================================================
0     success
1     unexpected/unclassified error
2     usage error (bad flags, unsupported values)
10    authentication or configuration problem
11    network or server-side failure
12    resource not found
13    validation error (bad input, conflicting state, missing extra)
===== =========================================================
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any, TypeVar

import click
import typer

from argilla_cli.io_utils import is_verbose, print_error

F = TypeVar("F", bound=Callable[..., Any])


class CLIError(Exception):
    """Base class for errors that map to a documented exit code."""

    exit_code: int = 1

    def __init__(self, message: str, exit_code: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        if exit_code is not None:
            self.exit_code = exit_code

    def __str__(self) -> str:
        return self.message


class UsageError(CLIError):
    exit_code = 2


class AuthConfigError(CLIError):
    exit_code = 10


class NetworkApiError(CLIError):
    exit_code = 11


class NotFoundError(CLIError):
    exit_code = 12


class ValidationError(CLIError):
    exit_code = 13


class MissingExtraError(ValidationError):
    """A feature needs an optional dependency group that isn't installed."""

    def __init__(self, feature: str, extra: str, packages: str) -> None:
        super().__init__(
            f"{feature} requires the '{extra}' extra. "
            f"Install it with: uv pip install '{packages}'"
        )


def _status_code_of(exc: BaseException) -> int | None:
    """Best-effort extraction of an HTTP status code from an exception.

    Argilla's API exceptions carry ``status_code`` directly; httpx/requests
    errors carry it on an attached response.
    """
    code = getattr(exc, "status_code", None)
    if isinstance(code, int):
        return code
    response = getattr(exc, "response", None)
    code = getattr(response, "status_code", None)
    if isinstance(code, int):
        return code
    return None


def _map_status(status: int, message: str) -> CLIError:
    if status in (401, 403):
        return AuthConfigError(message)
    if status == 404:
        return NotFoundError(message)
    if status in (400, 409, 422):
        return ValidationError(message)
    if status >= 500:
        return NetworkApiError(message)
    return CLIError(message)


def map_exception(exc: BaseException) -> CLIError:
    """Classify an arbitrary exception into a ``CLIError``.

    Classification is driven by exception *type* and HTTP status code. An
    earlier version matched substrings against the message, including a bare
    ``"5"`` test that reclassified any error whose text happened to contain
    the digit 5 -- a UUID, a dataset named ``ds5`` -- as a network failure.
    """
    if isinstance(exc, CLIError):
        return exc

    message = str(exc) or exc.__class__.__name__

    status = _status_code_of(exc)
    if status is not None:
        return _map_status(status, message)

    # Transport-level failures, without importing httpx/requests eagerly.
    module = type(exc).__module__.split(".")[0]
    name = type(exc).__name__
    if module in {"httpx", "requests", "urllib3"} or isinstance(
        exc, ConnectionError | TimeoutError
    ):
        return NetworkApiError(message)
    if module == "argilla":
        if "Credentials" in name or "Unauthorized" in name or "Forbidden" in name:
            return AuthConfigError(message)
        if "NotFound" in name:
            return NotFoundError(message)
        if "Conflict" in name or "Unprocessable" in name or "BadRequest" in name:
            return ValidationError(message)
        return NetworkApiError(message)

    return CLIError(message)


def exit_with_error(exc: BaseException, *, verbose: bool | None = None) -> None:
    """Print a concise message and exit with the mapped code."""
    mapped = map_exception(exc)
    show_detail = is_verbose() if verbose is None else verbose
    if show_detail and exc is not mapped:
        print_error(f"{mapped} ({type(exc).__name__}: {exc})")
    else:
        print_error(str(mapped))
    raise typer.Exit(code=mapped.exit_code)


def handle_errors(func: F) -> F:
    """Wrap a command body in the shared error handler.

    Control-flow exceptions are re-raised untouched. This matters more than it
    looks: ``typer.Exit`` and ``click.Abort`` both subclass ``RuntimeError``,
    so the hand-rolled ``except Exception`` blocks this decorator replaces
    swallowed deliberate exits -- turning a successful ``--exists-ok`` into a
    failure and rewriting documented exit codes into a generic 11.

    ``functools.wraps`` sets ``__wrapped__``, which ``inspect.signature``
    follows, so Typer still sees the original signature and builds the correct
    CLI parameters.
    """

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return func(*args, **kwargs)
        except (typer.Exit, typer.Abort, click.Abort, click.ClickException):
            raise
        except Exception as exc:  # noqa: BLE001 - deliberate catch-all boundary
            exit_with_error(exc)

    return wrapper  # type: ignore[return-value]
