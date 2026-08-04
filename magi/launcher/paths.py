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

import os
import sys
from pathlib import Path


_PLATFORM = sys.platform


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
