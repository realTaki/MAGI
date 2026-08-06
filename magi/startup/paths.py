"""Path helpers for the unified startup package.

Per plan §9:

- :func:`resolve_host_workspace` — operator's host root (``~/.magi``)
- :func:`resolve_magi_workspace` — per-MAGI slot under
  ``<host>/MAGI_Citizens/<name>``
- :func:`resolve_magis_database_path` — MAGIS public DB (``<host>/MAGI_Societies/...``)
- :func:`resolve_private_database_path` — MAGI's private SQLite file
- :func:`resolve_runtime_state_path` — runtime.json sidecar
- :func:`resolve_runtime_pid_path` / :func:`resolve_runtime_log_paths` — local
  process bookkeeping
- :func:`resolve_webui_pid_path` / :func:`resolve_webui_log_paths` —
  whole-MAGIS singleton WebUI

These helpers are *pure* — they take the root / name explicitly and do
not touch the process environment. Callers (the bootstrap / runtime /
local modules) read ``HOST_WORKSPACE_DIR`` / ``MAGI_NAME`` from
:class:`magi.startup.config.StartupConfig` and feed the values here.

The legacy ``magi.launcher.paths`` module still exposes
``MAGIC_DIR_NAME`` / ``MAGIS_DIR_NAME`` for tests; the canonical names
match the new module 1:1.
"""

from __future__ import annotations

from pathlib import Path

from magi.startup.config import (
    MAGI_CITIZENS_DIR,
    MAGI_SOCIETIES_DIR,
    StartupConfig,
)

# Sub-paths under a MAGI workspace.
_SKILLS_DIR = "skills"
_MEMORIES_DIR = "memories"
_LOGS_DIR = "logs"
_RUN_DIR = "run"

# Sidecar filename — written by bootstrap, read by every subsequent
# startup to enforce workspace identity consistency (plan §22).
RUNTIME_STATE_FILENAME = "runtime.json"
RUNTIME_PID_FILENAME = "magi.pid"
RUNTIME_LOG_STDOUT = "stdout.log"
RUNTIME_LOG_STDERR = "stderr.log"

# Singleton WebUI files — live under the host root, *not* under any
# individual MAGI workspace, because WebUI serves the whole MAGIS.
WEBUI_PID_FILENAME = "webui.pid"
WEBUI_LOG_STDOUT = "webui.stdout.log"
WEBUI_LOG_STDERR = "webui.stderr.log"


# ----------------------------------------------------------------------
# Host workspace
# ----------------------------------------------------------------------


def resolve_host_workspace(host_workspace_dir: Path) -> Path:
    """Canonicalise the operator's host workspace root."""
    return Path(host_workspace_dir).expanduser().resolve()


def resolve_magi_workspace(
    host_workspace_dir: Path,
    magi_name: str,
) -> Path:
    """Per-MAGI workspace — plan §6.

    ``<host>/MAGI_Citizens/<name>``
    """
    return resolve_host_workspace(host_workspace_dir) / MAGI_CITIZENS_DIR / magi_name


def from_config(cfg: StartupConfig) -> Path:
    """Convenience: derive the per-MAGI workspace from a :class:`StartupConfig`."""
    return cfg.workspace_dir


# ----------------------------------------------------------------------
# Database files
# ----------------------------------------------------------------------


def resolve_magis_database_path(
    host_workspace_dir: Path,
    magis_slug: str = "genesis",
    magis_id: int = 1,
) -> Path:
    """Per-MAGIS public SQLite location.

    Format: ``<host>/MAGI_Societies/<slug>-<id:02d>/magis.db``.
    """
    host = resolve_host_workspace(host_workspace_dir)
    return host / MAGI_SOCIETIES_DIR / f"{magis_slug}-{magis_id:02d}" / "magis.db"


def resolve_private_database_path(magi_workspace: Path) -> Path:
    """Per-MAGI private SQLite location.

    Format: ``<workspace>/memories/magi.db``.
    """
    return magi_workspace / _MEMORIES_DIR / "magi.db"


def magis_sqlite_url(magis_db_path: Path) -> str:
    """Render a SQLite URL suitable for ``MAGIS_DATABASE_URL``."""
    return f"sqlite:///{magis_db_path}"


# ----------------------------------------------------------------------
# Runtime state sidecar
# ----------------------------------------------------------------------


def resolve_runtime_state_path(magi_workspace: Path) -> Path:
    """Path to ``runtime.json`` — bootstrap identity sidecar (plan §22)."""
    return magi_workspace / RUNTIME_STATE_FILENAME


# ----------------------------------------------------------------------
# Local process bookkeeping
# ----------------------------------------------------------------------


def resolve_runtime_pid_path(magi_workspace: Path) -> Path:
    """Per-MAGI PID file — local process bookkeeping (plan §16)."""
    return magi_workspace / _RUN_DIR / RUNTIME_PID_FILENAME


def resolve_runtime_log_paths(magi_workspace: Path) -> tuple[Path, Path]:
    """Per-MAGI stdout / stderr log paths (plan §16)."""
    logs = magi_workspace / _LOGS_DIR
    return logs / RUNTIME_LOG_STDOUT, logs / RUNTIME_LOG_STDERR


# ----------------------------------------------------------------------
# Singleton WebUI
# ----------------------------------------------------------------------


def resolve_webui_pid_path(host_workspace_dir: Path) -> Path:
    """WebUI PID file — lives at the *host* root (plan §15)."""
    return resolve_host_workspace(host_workspace_dir) / _RUN_DIR / WEBUI_PID_FILENAME


def resolve_webui_log_paths(host_workspace_dir: Path) -> tuple[Path, Path]:
    """WebUI stdout / stderr — lives at the *host* root (plan §15)."""
    logs = resolve_host_workspace(host_workspace_dir) / _LOGS_DIR
    return logs / WEBUI_LOG_STDOUT, logs / WEBUI_LOG_STDERR


# ----------------------------------------------------------------------
# Workspace bootstrap helpers
# ----------------------------------------------------------------------


def ensure_workspace(magi_workspace: Path) -> Path:
    """Create the canonical per-MAGI workspace layout (idempotent).

    Always recreates: workspace/, workspace/skills/, workspace/memories/,
    workspace/logs/, workspace/run/.
    """
    magi_workspace.mkdir(parents=True, exist_ok=True)
    for sub in (_SKILLS_DIR, _MEMORIES_DIR, _LOGS_DIR, _RUN_DIR):
        (magi_workspace / sub).mkdir(parents=True, exist_ok=True)
    return magi_workspace


def ensure_host_workspace(host_workspace_dir: Path) -> Path:
    """Create the canonical host workspace layout (idempotent)."""
    host = resolve_host_workspace(host_workspace_dir)
    host.mkdir(parents=True, exist_ok=True)
    (host / MAGI_CITIZENS_DIR).mkdir(parents=True, exist_ok=True)
    (host / MAGI_SOCIETIES_DIR).mkdir(parents=True, exist_ok=True)
    (host / _RUN_DIR).mkdir(parents=True, exist_ok=True)
    (host / _LOGS_DIR).mkdir(parents=True, exist_ok=True)
    return host


__all__ = [
    "RUNTIME_STATE_FILENAME",
    "RUNTIME_PID_FILENAME",
    "RUNTIME_LOG_STDOUT",
    "RUNTIME_LOG_STDERR",
    "WEBUI_PID_FILENAME",
    "WEBUI_LOG_STDOUT",
    "WEBUI_LOG_STDERR",
    "resolve_host_workspace",
    "resolve_magi_workspace",
    "from_config",
    "resolve_magis_database_path",
    "resolve_private_database_path",
    "magis_sqlite_url",
    "resolve_runtime_state_path",
    "resolve_runtime_pid_path",
    "resolve_runtime_log_paths",
    "resolve_webui_pid_path",
    "resolve_webui_log_paths",
    "ensure_workspace",
    "ensure_host_workspace",
]