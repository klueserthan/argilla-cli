from __future__ import annotations

from dataclasses import dataclass
import typer

from argilla_cli.io_utils import print_error


@dataclass
class CLIError(Exception):
    message: str
    exit_code: int = 1

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.message


class UsageError(CLIError):
    """Exit code 2."""

    def __init__(self, message: str):
        super().__init__(message=message, exit_code=2)


class AuthConfigError(CLIError):
    """Exit code 10."""

    def __init__(self, message: str):
        super().__init__(message=message, exit_code=10)


class NetworkApiError(CLIError):
    """Exit code 11."""

    def __init__(self, message: str):
        super().__init__(message=message, exit_code=11)


class NotFoundError(CLIError):
    """Exit code 12."""

    def __init__(self, message: str):
        super().__init__(message=message, exit_code=12)


class ValidationError(CLIError):
    """Exit code 13."""

    def __init__(self, message: str):
        super().__init__(message=message, exit_code=13)


def map_exception(exc: Exception) -> CLIError:
    """Map generic exceptions to our CLIError hierarchy.

    Best-effort mapping based on common HTTP error codes in messages.
    """
    msg = str(exc) if exc else ""
    lower = msg.lower()
    if any(code in lower for code in ["401", "403", "unauthorized", "forbidden"]):
        return AuthConfigError("authentication/authorization failed")
    if "404" in lower or "not found" in lower:
        return NotFoundError("not found")
    if any(
        k in lower
        for k in [
            "timeout",
            "timed out",
            "5",
            "server error",
            "connection",
        ]
    ):
        return NetworkApiError("server/network issue; retry later")
    return CLIError(message=msg or "unexpected error", exit_code=1)


def exit_with_error(exc: Exception, *, verbose: bool = False) -> None:
    """Print a concise message and exit with mapped code."""
    mapped = map_exception(exc)
    if verbose:
        # Show original exception text
        print_error(f"{mapped}: {exc}")
    else:
        print_error(str(mapped))
    raise typer.Exit(code=mapped.exit_code)
