"""File reads and writes whose failure modes have to stay classified.

Both halves exist because the same mistake was made twice in separate
places, so they live together to be made once.

**Writing.** Files that must never be left half-written -- the record
export, where an interrupted run would destroy a previously good one, and
the credential store, where a truncated ``config.toml`` loses every stored
API key -- go to a temporary sibling and only replace the target once the
payload is complete. The temporary file shares the target's directory so
that ``os.replace`` is a same-filesystem rename, which is atomic.
``tempfile.mkstemp`` also creates it 0o600, which matters for the credential
store: writing in place and ``chmod``-ing afterwards leaves a window where
the file holding API keys is readable by everyone, and this has none.

**Reading.** ``UnicodeDecodeError`` derives from ``ValueError``, not
``OSError``, and lives in ``builtins`` -- so it slips through both the
obvious ``except OSError`` and ``map_exception``, and a user's file with one
bad byte exits 1 instead of the documented 13. That happened independently
in the mapping loader and the config loader; ``read_text_file`` is the one
place it now gets handled.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from argilla_cli.errors import ValidationError


def read_text_file(path: Path, description: str) -> str:
    """Read a UTF-8 text file, reporting failures as bad input.

    ``description`` names the file for the error message, e.g.
    ``"config file /home/u/.config/argilla-cli/config.toml"``.

    Decoding and I/O failures both land here: a file the user pointed the
    CLI at is user input, so an unreadable or undecodable one is a
    validation error (13), not an unexplained crash.
    """
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ValidationError(
            f"failed to read {description}: not valid UTF-8 ({exc})"
        ) from exc
    except OSError as exc:
        raise ValidationError(f"failed to read {description}: {exc}") from exc


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
