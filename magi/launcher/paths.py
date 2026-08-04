"""OS-specific data-root resolution for the Local launcher.

Per plan §5.1, the Local Profile defaults its data root to:

- macOS : ``~/Library/Application Support/MAGI``
- Linux : ``$XDG_DATA_HOME/magi`` (``~/.local/share/magi`` as fallback)
- Windows : ``%LOCALAPPDATA%\\MAGI`` (``~/AppData/Local/MAGI`` fallback)

The launcher-issued ``launcher.json``, ``control-secret`` and the
SQLite control registry live under ``<data_root>/control/``.

Mirrors what :meth:`magi.launcher.LocalPathLayout.from_platform` used
to do before the launch-pad consolidation; that factory method is
deleted but the convention lives on here so it can be reused across
:mod:`magi.launcher.cli` and the supervisor.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path


_PLATFORM = sys.platform

logger = logging.getLogger("magi.launcher.paths")


# Bundled default SOUL.md lives in ``prompts/`` so all prompt
# templates are co-located. The bootstrap copies it to the workspace
# root on first boot; the deployer can then edit the workspace copy
# without touching the source.
_BUNDLED_SOUL = Path(__file__).resolve().parent.parent / "prompts" / "soul.md"


def workspace_root(state_dir: str | os.PathLike[str]) -> Path:
    """Derive the workspace root from the state directory.

    The default layout puts the SQLite at
    ``<workspace>/state/magi.db``, so the workspace root is
    ``Path(state_dir).parent``. Used by every module that needs to
    resolve operator-facing paths (SOUL.md, skills/, memories/).
    """
    return Path(state_dir).parent


def bootstrap_workspace(workspace: Path) -> dict[str, str]:
    """Ensure the workspace has the canonical layout.

    Idempotent: every call only creates files / directories that are
    missing. Safe to run on every boot.

    Returns a small dict of ``{name: status}`` where status is either
    ``"created"`` (this call created the artifact) or ``"kept"`` (it
    was already there). The dict is purely informational — callers
    can ignore it.
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


def default_data_root() -> Path:
    """Return the OS-specific default data root for the Local Profile."""
    override = os.environ.get("MAGI_DATA_ROOT")
    if override:
        return Path(override)
    if _PLATFORM == "darwin":
        return Path.home() / "Library" / "Application Support" / "MAGI"
    if _PLATFORM == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "MAGI"
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg) / "magi"
    return Path.home() / ".local" / "share" / "magi"


def control_dir(data_root: Path) -> Path:
    """Return the ``<data_root>/control`` directory, creating it on demand."""
    p = Path(data_root).expanduser().resolve() / "control"
    p.mkdir(parents=True, exist_ok=True)
    return p


def control_secret_path(control_dir: Path) -> Path:
    """Path to the launcher-issued control secret file (0600, plan §11)."""
    return Path(control_dir) / "control-secret"


def launcher_state_path(control_dir: Path) -> Path:
    """Path to the launcher state JSON (``launcher.json``)."""
    return Path(control_dir) / "launcher.json"


def runtime_workspace_root(data_root: Path, runtime_id: int, slug: str) -> Path:
    """Resolve the per-runtime workspace root.

    Format: ``<data_root>/MAGIC/<runtime_id>-<slug>/workspace/``.
    """
    return Path(data_root) / "MAGIC" / f"{runtime_id}-{slug}" / "workspace"


def runtime_log_dir(data_root: Path, runtime_id: int, slug: str) -> Path:
    return runtime_workspace_root(data_root, runtime_id, slug) / "logs"


def runtime_audit_log_path(data_root: Path, runtime_id: int, slug: str) -> Path:
    return runtime_workspace_root(data_root, runtime_id, slug) / "audit.log"


__all__ = [
    "default_data_root",
    "control_dir",
    "control_secret_path",
    "launcher_state_path",
    "runtime_workspace_root",
    "runtime_log_dir",
    "runtime_audit_log_path",
]
