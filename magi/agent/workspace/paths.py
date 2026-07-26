"""Workspace path resolution.

The workspace root is **always** the parent of the state
directory. Inside the container the state directory is
``/workspace/memories``, so the workspace root is ``/workspace``
— a fixed address that cannot be changed.

The workspace holds persistent artifacts that are NOT the
settings DB:

  - ``skills/``    : per-node skill bundle (C4 — SkillRunner)
  - ``SOUL.md``    : the EVE\'s "soul" — its persona, voice,
                     rules of engagement. Read as the agent
                     loop\'s system-prompt prefix.

There is no ``MAGI_WORKSPACE_DIR`` env-var override — the
host-side mount path is a docker-compose concern, not a
runtime concern. Future path resolution (e.g. per-tenant
subdirs, encrypted volumes) lives here. The bootstrap that
creates these directories is in :mod:`.bootstrap`.
"""

from __future__ import annotations

import os
from pathlib import Path


def workspace_root(state_dir: str | os.PathLike[str]) -> Path:
    """Derive the workspace root from the state directory.

    The default layout puts the SQLite at
    ``/workspace/memories/magi.db``, so the workspace root is
    ``/workspace``.  The container bind-mount target is fixed
    at ``/workspace`` — this is not user-overridable.
    """
    return Path(state_dir).parent
