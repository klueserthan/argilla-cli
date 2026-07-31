"""argilla-cli: manage Argilla servers from the terminal."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

__all__ = ["__version__"]

try:
    __version__ = version("argilla-cli")
except PackageNotFoundError:  # pragma: no cover - source checkout without install
    __version__ = "0.0.0+unknown"
