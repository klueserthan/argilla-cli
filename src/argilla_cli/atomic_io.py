"""Atomic file replacement.

Two places rewrite files that must never be left half-written: the record
exporter, where an interrupted run would destroy a previously good export,
and the credential store, where a truncated ``config.toml`` loses every
stored API key. Both write to a temporary sibling and only replace the
target once the payload is complete, so a reader always sees one whole
version or the other.

The temporary file shares the target's directory so that ``os.replace`` is a
same-filesystem rename, which is atomic. ``tempfile.mkstemp`` also creates it
0o600, which matters for the credential store: writing in place and
``chmod``-ing afterwards leaves a window where the file holding API keys is
readable by everyone, and this has no such window.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def atomic_path(path: Path) -> Iterator[Path]:
    """Yield a temporary path that replaces ``path`` on a clean exit.

    Cleanup catches ``BaseException`` rather than ``Exception``:
    ``KeyboardInterrupt`` does not derive from ``Exception``, and an export
    or a config write is exactly the kind of operation someone interrupts.
    Narrowing this would leave the ``.partial`` file behind.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    handle_fd, tmp_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".partial"
    )
    os.close(handle_fd)
    tmp_path = Path(tmp_name)

    try:
        yield tmp_path
        os.replace(tmp_path, path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
