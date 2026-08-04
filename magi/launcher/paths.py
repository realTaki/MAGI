"""OS-specific + deployer-supplied path resolution for the launcher.

This module is the **single** place MAGI's filesystem layout is
defined.  Two path families live here:

1. **Deployer-supplied workspace** — the operator's persistent
   volume (container bind-mount, Local Profile data root, test
   ``tmp_path``).

   - :func:`workspace_dir` reads ``$MAGI_WORKSPACE_DIR`` (or falls
     back to ``/workspace`` for the K8s-mount default).
   - :func:`state_dir` derives the SQLite + sessions directory as
     ``<workspace_dir>/memories``.

   Derived from ``MAGI_WORKSPACE_DIR``
   constants — the deployer's only filesystem knob is
   ``MAGI_WORKSPACE_DIR``.

2. **Local Profile launcher** — the Local Profile's per-runtime /
   control / launcher-state layout.  Per plan §5.1 the OS-specific
   default lives at ``default_data_root``; control
   registry / launcher state live under ``<data_root>/control/``;
   per-runtime workspaces / logs derive from the runtime_id + slug.

3. **K8s-vs-Local state workspace root** — :func:`workspace_root`
   derives the operator-facing files (SOUL.md, skills/, memories/)
   from a state directory.

The architecture test treats ``magi.launcher`` as a Composition-Root
prefix; callers across the bus import these helpers for read-only
configuration values, not for cross-package wiring.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path


_PLATFORM = sys.platform

logger = logging.getLogger("magi.launcher.paths")


# ──────────────────────────────────────────────────────────────────────── #
# Group 1: deployer-supplied workspace (container / K8s profile)
# ──────────────────────────────────────────────────────────────────────── #

# Fixed canonical subdirectory under the workspace that holds the SQLite
# database, alembic migrations, and session history.  Not configurable —
# this is the schema the runtime assumes.
_STATE_SUBDIR = "memories"


def workspace_dir() -> Path:
    """Return the deployer's persistent workspace directory.

    Resolution:

    1. ``$MAGI_WORKSPACE_DIR`` when set (deployer configuration);
    2. ``/workspace`` (K8s-mount default) when not set.

    This is the **only** host-level environment variable for path
    layout.  The single deployer env var is ``MAGI_WORKSPACE_DIR``
    var — those values are derived from this one.
    """
    raw = os.environ.get("MAGI_WORKSPACE_DIR")
    return Path(raw).expanduser().resolve() if raw else Path("/workspace")


def state_dir() -> Path:
    """Return the SQLite + migrations + session-history directory.

    Always ``<workspace_dir>/<STATE_SUBDIR>``; never set independently.
    Local Profile that uses a different layout reads the layout's
    ``state_dir`` directly and never consults this helper.
    """
    return workspace_dir() / _STATE_SUBDIR


def workspace_root() -> Path:
    """Alias for :func:`workspace_dir` — 0-arg workspace root resolver.

    Use this when you need a workspace ``Path`` without knowing about
    ``MAGI_WORKSPACE_DIR``.  Replaces the legacy
    ``workspace_root(state_dir)`` pattern where callers passed a
    ``state_dir`` they had to look up themselves.
    """
    return workspace_dir()


# Bundled default SOUL.md lives in ``prompts/`` so all prompt
# templates are co-located. The bootstrap copies it to the workspace
# root on first boot; the deployer can then edit the workspace copy
# without touching the source.
_BUNDLED_SOUL = Path(__file__).resolve().parent.parent / "prompts" / "soul.md"


def workspace_root_from_state(state_dir: str | os.PathLike[str]) -> Path:
    """DEPRECATED — derive the workspace root from a state directory.

    Kept for the launcher / agent / api modules that still thread
    ``state_dir`` through their constructors. New code should call
    the 0-arg :func:`workspace_root` (or :func:`workspace_dir`)
    instead.  This function will be removed in a later phase once all
    internal callers stop passing ``state_dir`` explicitly.
    """
    return Path(state_dir).parent


# Backward-compat alias — the original ``workspace_root(state_dir)``
# name.  Internal modules currently import this; once Phase D1 / D2
# drops ``state_dir`` from those constructors, the alias can go too.
# (Kept separate from ``workspace_root()`` — they have different
# signatures so they can't both be named ``workspace_root``.)


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
    # Group 1 — deployer workspace (container / K8s profile)
    "workspace_dir",
    "state_dir",
    "workspace_root",
    "bootstrap_workspace",
    # Group 2 — Local Profile data root
    "default_data_root",
    # Group 3 — per-runtime and control-plane
    "control_dir",
    "control_secret_path",
    "launcher_state_path",
    "runtime_workspace_root",
    "runtime_log_dir",
    "runtime_audit_log_path",
    # Deprecated
    "workspace_root_from_state",
]
