"""MAGI workspace — path resolution + bootstrap.

Holds the EVE\'s persistent artifacts that are NOT the
settings DB:

  - ``skills/``    : per-node skill bundle (C4 — SkillRunner)
  - ``memories/``  : per-node memory (C5 — proactive + context)
  - ``SOUL.md``    : the EVE\'s "soul" — its persona, voice,
                     rules of engagement.

Layout
------

  - :mod:`.paths`     — :func:`workspace_root` + path helpers
  - :mod:`.bootstrap` — :func:`bootstrap_workspace` (idempotent
                        first-boot directory creation)

Future extensions (per-tenant workspaces, encrypted volumes,
skill bundle install, memory migration, etc.) land as new
submodules here, not as inline additions to this file.
"""

from __future__ import annotations

import logging

from magi.agent.workspace.bootstrap import bootstrap_workspace
from magi.agent.workspace.paths import workspace_root


logger = logging.getLogger("magi.agent.workspace")


__all__ = ["workspace_root", "bootstrap_workspace"]
