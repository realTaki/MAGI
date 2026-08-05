"""Launcher-issued control secret.

The Local Profile creates a 256-bit URL-safe random secret the first
time ``magi local start`` runs, writes it to ``<magis_home>/control-secret``
with file-mode ``0600``, and the Bus-store mirrors a salted SHA-256
digest in the MAGIS database.  The raw secret is required by the
loopback-only control-plane HTTP API (``X-MAGI-Control-Secret`` header).

The secret is intentionally a launcher concern, not a Business module
concern.  Only :mod:`magi.launcher.cli` touches the raw bytes; the
:class:`magi.bus.services.control_registry.ControlRegistryService`
only ever sees the salted hash via :meth:`put_secret` / :meth:`verify_secret`.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path


def ensure_control_secret(path: Path) -> str:
    """Return the persisted secret; generate a new one if missing.

    ``path`` is the on-disk file (``<magis_home>/control-secret``).
    The file is forced to ``0600`` on POSIX systems.
    """

from __future__ import annotations

import os
import secrets
from pathlib import Path


def ensure_control_secret(path: Path) -> str:
    """Return the persisted secret; generate a new one if missing.

    ``path`` is the on-disk file (``<control_dir>/control-secret``).
    The file is forced to ``0600`` on POSIX systems.
    """
    path = Path(path)
    if path.exists():
        raw = path.read_text(encoding="utf-8").strip()
        if raw:
            return raw
    new = secrets.token_urlsafe(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(new, encoding="utf-8")
    _chmod_0600(path)
    return new


def reveal_control_secret(path: Path) -> str:
    """Read the persisted secret for callers that hold the file mode."""
    return Path(path).read_text(encoding="utf-8").strip()


def _chmod_0600(path: Path) -> None:
    """POSIX-only; on Windows the file ACLs default to the user anyway."""
    if os.name == "posix":
        os.chmod(path, 0o600)


__all__ = ["ensure_control_secret", "reveal_control_secret"]
