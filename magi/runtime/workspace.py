"""MAGI workspace bootstrap.

The workspace root (default ``/workspace`` inside the container;
``MAGI_WORKSPACE_DIR`` overrides) holds the EVE's persistent
artifacts that are *not* the settings DB:

  - ``skills/``    : per-node skill bundle (C4 — SkillRunner)
  - ``memories/``  : per-node memory (C5 — proactive + context)
  - ``SOUL.md``    : the EVE's "soul" — its persona, voice,
                     rules of engagement. Read as the agent
                     loop's system-prompt prefix.

On first boot we ensure these exist so subsequent code can
assume the layout. The bootstrap is idempotent — running it on
every boot is cheap and self-healing (it only creates files /
directories that are missing, never overwrites deployer edits).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger("magi.runtime.workspace")

# Bundled default SOUL.md lives in ``prompts/`` so all
# prompt templates are co-located.  The bootstrap copies it
# to the workspace root on first boot; the deployer can then
# edit the workspace copy without touching the source.
_BUNDLED_SOUL = Path(__file__).resolve().parent / "prompts" / "soul.md"


def workspace_root(state_dir: str | os.PathLike[str]) -> Path:
    """Derive the workspace root from the state directory.

    The default layout puts the SQLite at ``<root>/state/magi.db``
    (see ``magi.runtime.state.init_sqlite``), so the workspace
    root is the parent of the state directory. If a future
    deployer sets ``MAGI_WORKSPACE_DIR`` directly (state lives
    outside the workspace tree), the override is honored.

    Falls back to ``/workspace`` if neither path can be derived.
    """
    override = os.environ.get("MAGI_WORKSPACE_DIR")
    if override:
        return Path(override)
    return Path(state_dir).parent


def bootstrap_workspace(workspace: Path) -> dict[str, str]:
    """Ensure the workspace has the canonical layout.

    Idempotent: every call only creates files / directories
    that are missing. Safe to run on every boot.

    Returns a small dict of ``{name: status}`` where status is
    either ``"created"`` (this call created the artifact) or
    ``"kept"`` (it was already there). The dict is purely
    informational — callers can ignore it.
    """
    workspace.mkdir(parents=True, exist_ok=True)
    created: dict[str, str] = {"workspace_root": "kept"}

    skills = workspace / "skills"
    if not skills.exists():
        skills.mkdir(parents=True, exist_ok=True)
        created["skills/"] = "created"
    else:
        created["skills/"] = "kept"

    memories = workspace / "memories"
    if not memories.exists():
        memories.mkdir(parents=True, exist_ok=True)
        created["memories/"] = "created"
    else:
        created["memories/"] = "kept"

    soul = workspace / "SOUL.md"
    if not soul.exists():
        if not _BUNDLED_SOUL.is_file():
            logger.error(
                "bundled soul.md missing at %s; workspace SOUL.md not created",
                _BUNDLED_SOUL,
            )
            created["SOUL.md"] = "skipped (no bundled default)"
        else:
            default_text = _BUNDLED_SOUL.read_text(encoding="utf-8")
            soul.write_text(default_text, encoding="utf-8")
            created["SOUL.md"] = "created"
    else:
        created["SOUL.md"] = "kept"

    created_items = [k for k, v in created.items() if v == "created"]
    if created_items:
        logger.info(
            "workspace bootstrap created: %s",
            ", ".join(created_items),
            extra={"workspace": str(workspace)},
        )
    else:
        logger.info(
            "workspace bootstrap ok (everything present)",
            extra={"workspace": str(workspace)},
        )
    return created
