"""Explicit workspace and topology provisioning for MAGI.

Opening a BUS synchronises its existing databases before exposing Books.  This
module remains the sole production owner of first-time workspace creation,
MAGI/MAGIS identity setup, and node defaults.  It is called by provisioning
commands only; a runtime uses :func:`bus.open_bus` instead.
"""

from __future__ import annotations

import secrets
from pathlib import Path


class StorageNotProvisioned(RuntimeError):
    """Raised when node topology/storage has not been created yet."""


def provision_node_storage(
    *,
    state_dir: str,
    magis_url: str | None,
):
    """Provision one fresh node store and its MAGIS store when supplied.

    ``state_dir`` must be the canonical ``<workspace>/memories`` directory.
    Provisioning is idempotent for the current revision, but it never accepts
    the retired ``<workspace>/magi.db`` path: callers must start from a clean
    state as part of this architecture cutover.
    """
    state_path = Path(state_dir).resolve()
    if state_path.name != "memories":
        raise ValueError("node state_dir must end in 'memories'")
    workspace_dir = state_path.parent
    retired_db = workspace_dir / "magi.db"
    if retired_db.exists():
        raise StorageNotProvisioned(
            f"retired node database exists at {retired_db}; clean the workspace before provisioning"
        )

    workspace_dir.mkdir(parents=True, exist_ok=True)
    for name in ("memories", "prompts", "skills", "logs", "run"):
        (workspace_dir / name).mkdir(parents=True, exist_ok=True)

    # Importing and wiring Books registers all BUS-owned metadata before the
    # one explicit schema synchronisation below.
    from old_bus.bootstrap import _open_with_dirs

    bus = _open_with_dirs(
        state_dir=str(state_path),
        magis_url=magis_url,
        allow_unprovisioned=True,
    )
    bus.messages_book.ensure_fts()
    if not bus.settings_book.get_value(key="auth.signing_key"):
        bus.settings_book.set(key="auth.signing_key", value=secrets.token_hex(32))
    return bus


__all__ = [
    "StorageNotProvisioned",
    "provision_node_storage",
]
