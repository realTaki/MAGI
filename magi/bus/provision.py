"""Explicit workspace and topology provisioning for MAGI.

Opening a BUS synchronises its existing databases before exposing Books.  This
module remains the sole production owner of first-time workspace creation,
MAGI/MAGIS identity setup, and node defaults.  It is called by provisioning
commands only; a runtime uses :func:`magi.bus.open_bus` instead.
"""

from __future__ import annotations

import json
import secrets
from pathlib import Path


class StorageNotProvisioned(RuntimeError):
    """Raised when node topology/storage has not been created yet."""


def provision_node_storage(
    *,
    state_dir: str,
    magis_url: str | None,
    prompts_dir: str | None = None,
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
    for name in ("memories", "skills", "logs", "run"):
        (workspace_dir / name).mkdir(parents=True, exist_ok=True)

    # Importing and wiring Books registers all BUS-owned metadata before the
    # one explicit schema synchronisation below.
    from magi.bus.bootstrap import (
        _ensure_workspace_soul,
        _open_with_dirs,
        _resolve_prompts_dir,
    )

    bus = _open_with_dirs(
        state_dir=str(state_path),
        magis_url=magis_url,
        prompts_dir=prompts_dir,
        allow_unprovisioned=True,
    )
    bus.messages_book.ensure_fts()
    if not bus.settings_book.get(key="auth.signing_key"):
        bus.settings_book.set(key="auth.signing_key", value=secrets.token_hex(32))
    # ``channels.enabled`` is the runtime's single source of truth for which
    # channel workers to start. WebUI is required for the operator dashboard;
    # A2A is MAGIS-internal durable work and is not a channel worker.
    from magi.bus.library.local.tasksBook import Channel

    if not bus.settings_book.get(key="channels.enabled"):
        # Use :data:`Channel` enum values rather than string literals
        # so renaming the enum keeps the persisted default in sync.
        bus.settings_book.set(
            key="channels.enabled",
            value=json.dumps([Channel.WEBUI.value]),
        )
    resolved_prompts = _resolve_prompts_dir(prompts_dir)
    if resolved_prompts is not None:
        _ensure_workspace_soul(workspace_dir, resolved_prompts)
    return bus


__all__ = [
    "StorageNotProvisioned",
    "provision_node_storage",
]
