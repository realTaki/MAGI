"""Workspace path resolution.

The workspace root (default ``/workspace`` inside the container;
``MAGI_WORKSPACE_DIR`` overrides) holds the EVE\'s persistent
artifacts that are NOT the settings DB:

  - ``skills/``    : per-node skill bundle (C4 — SkillRunner)
  - ``memories/``  : per-node memory (C5 — proactive + context)
  - ``SOUL.md``    : the EVE\'s "soul" — its persona, voice,
                     rules of engagement. Read as the agent
                     loop\'s system-prompt prefix.

Future path resolution (e.g. per-tenant subdirs, encrypted
volumes, multi-workspace fan-out) lives here. The bootstrap
that creates these directories is in :mod:`.bootstrap`.
"""

from __future__ import annotations

import os
from pathlib import Path


def workspace_root(state_dir: str | os.PathLike[str]) -> Path:
    """Derive the workspace root from the state directory.

    The default layout puts the SQLite at ``<root>/state/magi.db``
    (see ``magi.agent.state.init_sqlite``), so the workspace
    root is the parent of the state directory. If a future
    deployer sets ``MAGI_WORKSPACE_DIR`` directly (state lives
    outside the workspace tree), the override is honored.

    Falls back to ``/workspace`` if neither path can be derived.
    """
    override = os.environ.get("MAGI_WORKSPACE_DIR")
    if override:
        return Path(override)
    return Path(state_dir).parent
