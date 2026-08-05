"""OS-specific + deployer-supplied path resolution for the launcher.

This module is the **single** place MAGI's filesystem layout is
defined.  Two path families live here:

1. **Deployer-supplied workspace** — the operator's persistent
   volume (container bind-mount, Local Profile data root, test
   ``tmp_path``).

   - :func:`workspace_dir` reads ``$MAGI_WORKSPACE_DIR`` (K8s Pod)
     or derives from ``$MAGI_DATA_ROOT`` (Local Profile).
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
# this is the schema the runtime assumes.  Shared by K8s and Local Profile
# alike so ``workspace/memories/magi.db`` is the single truth.
_STATE_SUBDIR = "memories"


def workspace_dir() -> Path:
    """Return the deployer's persistent workspace directory.

    Resolution — three branches, no host-root fallback:

    1. ``$MAGI_WORKSPACE_DIR`` when set (K8s Pod / explicitly configured).
    2. ``$MAGI_DATA_ROOT`` + ``$MAGI_RUNTIME_SLUG`` when set (Local
       Profile runtime process) → ``<data_root>/MAGIC/<slug>/workspace``.
    3. ``$MAGI_DATA_ROOT`` set, no ``$MAGI_RUNTIME_SLUG`` (Local Profile
       launcher) → ``<data_root>/MAGIS/genesis-01/launcher-workspace``.

    The slug alone is sufficient — ``EVA-000`` is both the MAGIC display
    name and the directory key, so ``MAGI_RUNTIME_ID`` is unnecessary
    for path resolution.
    """
    raw = os.environ.get("MAGI_WORKSPACE_DIR")
    if raw:
        return Path(raw).expanduser().resolve()
    data_root = os.environ.get("MAGI_DATA_ROOT")
    if data_root:
        runtime_slug = os.environ.get("MAGI_RUNTIME_SLUG")
        if runtime_slug:
            return Path(data_root) / "MAGIC" / runtime_slug / "workspace"
        return magis_home(Path(data_root)) / "launcher-workspace"
    raise RuntimeError(
        "workspace_dir() needs MAGI_WORKSPACE_DIR (K8s Pod) or "
        "MAGI_DATA_ROOT (Local Profile). Neither is set."
    )


def state_dir() -> Path:
    """Return the SQLite + migrations + session-history directory.

    One resolver, three branches — one per process role:

    1. **Local Profile, runtime process** —
       ``MAGI_DATA_ROOT`` + ``MAGI_RUNTIME_SLUG`` set.
       State lives at ``<data_root>/MAGIC/<slug>/workspace/memories/``.
    2. **Local Profile, launcher** —
       ``MAGI_DATA_ROOT`` set, no ``MAGI_RUNTIME_SLUG``.
       Scratch SQLite at ``<data_root>/MAGIS/genesis-01/launcher-state``.
    3. **K8s Profile** — no ``MAGI_DATA_ROOT``.
       State at ``<workspace_dir>/memories``.
    """
    data_root = os.environ.get("MAGI_DATA_ROOT")
    if data_root:
        runtime_slug = os.environ.get("MAGI_RUNTIME_SLUG")
        if runtime_slug:
            return (
                Path(data_root).expanduser().resolve()
                / "MAGIC"
                / runtime_slug
                / "workspace"
                / _STATE_SUBDIR
            )
        return magis_home(Path(data_root)) / "launcher-state"
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
    """Return the OS-specific default data root for the Local Profile.

    The Local Profile follows the openclaw-style layout:

    - Linux: ``~/.magi``
    - macOS: ``~/Documents/.magi``
    - Windows: ``%USERPROFILE%\\Documents\\.magi``

    macOS / Windows intentionally place the data root under the user's
    Documents folder rather than the OS-managed Application Support /
    AppData location — openclaw-style operators expect to find a single
    ``.magi`` folder they can browse, back up, and sync via the same
    mechanism as any other document.  Linux keeps the traditional
    ``~/.magi`` to mirror the same single-folder experience; XDG
    ``$XDG_DATA_HOME/magi`` is honored as an explicit override.

    ``$MAGI_DATA_ROOT`` always wins so tests / power users can route the
    data root anywhere.
    """
    override = os.environ.get("MAGI_DATA_ROOT")
    if override:
        return Path(override)
    if _PLATFORM == "darwin":
        return Path.home() / "Documents" / ".magi"
    if _PLATFORM == "win32":
        return Path.home() / "Documents" / ".magi"
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg) / "magi"
    return Path.home() / ".magi"


def magis_home(data_root: Path) -> Path:
    """Return the first MAGIS directory (``<data_root>/MAGIS/genesis-01/``).

    Delegates to :func:`magis_dir` with the default Genesis id/slug.
    This is where the launcher secret and state live, co-located with
    the MAGIS database.
    """
    p = magis_dir(data_root, 1, "genesis")
    p.mkdir(parents=True, exist_ok=True)
    return p


def control_secret_path(magis_home: Path) -> Path:
    """Path to the launcher-issued control secret file (0600).

    Lives alongside the MAGIS database so all control-plane state is
    in one place.
    """
    return Path(magis_home) / "control-secret"


def launcher_state_path(magis_home: Path) -> Path:
    """Path to the launcher state JSON (``launcher.json``).

    Lives alongside the MAGIS database.
    """
    return Path(magis_home) / "launcher.json"


def runtime_workspace_root(data_root: Path, runtime_id: int, slug: str) -> Path:
    """Resolve the per-runtime workspace root.

    Format: ``<data_root>/MAGIC/<slug>/workspace/``.  The *slug* is the
    MAGIC's directory-safe name (e.g. ``eva-000``, ``eva-001``);
    ``runtime_id`` is accepted for call-site compatibility but not
    embedded in the path — the slug alone is the unique key.
    """
    return Path(data_root) / "MAGIC" / slug / "workspace"


def runtime_state_dir(data_root: Path, runtime_id: int, slug: str) -> Path:
    """Resolve the per-runtime SQLite directory.

    Format: ``<data_root>/MAGIC/<slug>/workspace/memories/``.
    Mirrors the K8s ``<workspace_dir>/memories`` convention so every
    profile resolves to ``workspace/memories/magi.db``.  Used by
    :class:`magi.launcher.LocalPathLayout` and by the Local subprocess's
    path resolution via :func:`state_dir`.
    """
    return Path(data_root) / "MAGIC" / slug / "workspace" / "memories"


def runtime_log_dir(data_root: Path, runtime_id: int, slug: str) -> Path:
    return runtime_workspace_root(data_root, runtime_id, slug) / "logs"


def runtime_audit_log_path(data_root: Path, runtime_id: int, slug: str) -> Path:
    return runtime_workspace_root(data_root, runtime_id, slug) / "audit.log"


def magis_dir(data_root: Path, magis_id: int, slug: str) -> Path:
    """Resolve the per-MAGIS public SQLite directory.

    Format: ``<data_root>/MAGIS/<slug>-<magis_id:02d>/``.  Name first,
    id last — same style as MAGIC's ``eva-000``.  The first MAGIS
    seeded by the Local Profile is Genesis with magis_id=1 and
    slug="genesis", so its directory is
    ``<data_root>/MAGIS/genesis-01/magis.db``.
    """
    return Path(data_root) / "MAGIS" / f"{slug}-{magis_id:02d}"


def magis_db_path(data_root: Path, magis_id: int, slug: str) -> Path:
    """Convenience: per-MAGIS SQLite file path."""
    return magis_dir(data_root, magis_id, slug) / "magis.db"


__all__ = [
    # Group 1 — deployer workspace (container / K8s profile)
    "workspace_dir",
    "state_dir",
    "workspace_root",
    "bootstrap_workspace",
    # Group 2 — Local Profile data root
    "default_data_root",
    # Group 3 — per-runtime and control-plane
    "magis_home",
    "control_secret_path",
    "launcher_state_path",
    "runtime_workspace_root",
    "runtime_state_dir",
    "runtime_log_dir",
    "runtime_audit_log_path",
    "magis_dir",
    "magis_db_path",
    # Deprecated
    "workspace_root_from_state",
]
