"""Workspace bootstrap — first-boot directory creation.

Idempotent: every call only creates files / directories
that are missing. Safe to run on every boot.

Returns a small dict of ``{name: status}`` where status is
either ``"created"`` (this call created the artifact) or
``"kept"`` (it was already there). The dict is purely
informational — callers can ignore it.
"""

from __future__ import annotations

import logging
from pathlib import Path


logger = logging.getLogger("magi.agent.workspace.bootstrap")

# Bundled default SOUL.md lives in ``prompts/`` so all
# prompt templates are co-located. The bootstrap copies it
# to the workspace root on first boot; the deployer can then
# edit the workspace copy without touching the source.
_BUNDLED_SOUL = Path(__file__).resolve().parent.parent / "prompts" / "soul.md"


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
