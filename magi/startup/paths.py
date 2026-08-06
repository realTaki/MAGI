"""Startup path resolution — every filesystem path needed by the launcher.

All functions are pure (no env reads, no side effects).  Callers pass
the host workspace directory explicitly so tests can inject tmp paths.

Layout (per refactor plan §7, §9):

.. code-block:: text

    <HOST_WORKSPACE_DIR>/
    ├── MAGI_Citizens/
    │   ├── eva-000/
    │   │   ├── magi.db           # private SQLite
    │   │   ├── runtime.json      # runtime state (identity record)
    │   │   ├── skills/           # SKILL.md files
    │   │   ├── memories/         # memory subsystem data
    │   │   ├── logs/             # stdout / stderr
    │   │   │   ├── stdout.log
    │   │   │   └── stderr.log
    │   │   └── run/
    │   │       └── magi.pid
    │   └── eva-001/
    │       └── ...
    ├── MAGI_Societies/
    │   └── genesis/
    │       └── magis.db          # MAGIS public SQLite
    ├── run/
    │   └── webui.pid
    └── logs/
        ├── webui.stdout.log
        └── webui.stderr.log
"""

from __future__ import annotations

from pathlib import Path


# ------------------------------------------------------------------
# host workspace
# ------------------------------------------------------------------

def resolve_host_workspace() -> Path:
    """Return the default host workspace directory.

    Respects ``HOST_WORKSPACE_DIR`` env var; falls back to ``~/.magi``.
    This is the *only* function that reads the environment.
    """
    import os
    raw = os.environ.get("HOST_WORKSPACE_DIR")
    if raw:
        return Path(raw).expanduser().resolve()
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg).expanduser().resolve() / "magi"
    return Path.home() / ".magi"


# ------------------------------------------------------------------
# MAGI workspace
# ------------------------------------------------------------------

def resolve_magi_workspace(host_workspace_dir: Path, magi_name: str) -> Path:
    """Derive the MAGI workspace from host root and name.

    Always: ``<host>/MAGI_Citizens/<magi_name>/``
    """
    return host_workspace_dir / "MAGI_Citizens" / magi_name


# ------------------------------------------------------------------
# databases
# ------------------------------------------------------------------

def resolve_magis_database_path(host_workspace_dir: Path) -> Path:
    """Return the default MAGIS SQLite path for the first MAGIS.

    ``<host>/MAGI_Societies/genesis/magis.db``
    """
    return host_workspace_dir / "MAGI_Societies" / "genesis" / "magis.db"


def resolve_private_database_path(workspace_dir: Path) -> Path:
    """Return the private SQLite path for one MAGI.

    ``<workspace>/magi.db``
    """
    return workspace_dir / "magi.db"


def resolve_private_database_url(workspace_dir: Path) -> str:
    """Return a ``sqlite:///...`` URL for the private database."""
    db_path = resolve_private_database_path(workspace_dir)
    return f"sqlite:///{db_path}"


def resolve_magis_database_url(host_workspace_dir: Path) -> str:
    """Return a ``sqlite:///...`` URL for the default MAGIS database."""
    db_path = resolve_magis_database_path(host_workspace_dir)
    return f"sqlite:///{db_path}"


# ------------------------------------------------------------------
# runtime state
# ------------------------------------------------------------------

def resolve_runtime_state_path(workspace_dir: Path) -> Path:
    """Path to ``runtime.json`` — persisted identity record.

    Contains ``{"magi_id": ..., "magis_database_url": ...}``.
    Used to detect workspace identity conflicts (§22.2).
    """
    return workspace_dir / "runtime.json"


def resolve_runtime_pid_path(workspace_dir: Path) -> Path:
    """Path to the per-MAGI PID file.

    ``<workspace>/run/magi.pid``
    """
    return workspace_dir / "run" / "magi.pid"


def resolve_runtime_log_paths(workspace_dir: Path) -> tuple[Path, Path]:
    """Return ``(stdout_path, stderr_path)`` for one MAGI.

    ``<workspace>/logs/stdout.log``, ``<workspace>/logs/stderr.log``
    """
    log_dir = workspace_dir / "logs"
    return (log_dir / "stdout.log", log_dir / "stderr.log")


# ------------------------------------------------------------------
# WebUI (singleton — lives at host level, not per-MAGI)
# ------------------------------------------------------------------

def resolve_webui_pid_path(host_workspace_dir: Path) -> Path:
    """Path to the singleton WebUI PID file.

    ``<host>/run/webui.pid`` — WebUI belongs to the whole MAGIS.
    """
    return host_workspace_dir / "run" / "webui.pid"


def resolve_webui_log_paths(host_workspace_dir: Path) -> tuple[Path, Path]:
    """Return ``(stdout_path, stderr_path)`` for the singleton WebUI.

    ``<host>/logs/webui.stdout.log``, ``<host>/logs/webui.stderr.log``
    """
    log_dir = host_workspace_dir / "logs"
    return (log_dir / "webui.stdout.log", log_dir / "webui.stderr.log")


# ------------------------------------------------------------------
# directory bootstrapping (idempotent)
# ------------------------------------------------------------------

def ensure_host_workspace(host_workspace_dir: Path) -> Path:
    """Create the host workspace root directory if missing.

    Returns the resolved, guaranteed-to-exist directory.
    """
    host_workspace_dir.mkdir(parents=True, exist_ok=True)
    return host_workspace_dir


def ensure_workspace(workspace_dir: Path) -> Path:
    """Create the per-MAGI workspace and its canonical subdirectories.

    Creates (if missing):
    - ``<workspace>/`` (root)
    - ``<workspace>/skills/``
    - ``<workspace>/memories/``
    - ``<workspace>/logs/``
    - ``<workspace>/run/``
    - ``<workspace>/SOUL.md`` (from bundled default if absent)

    Returns the guaranteed-to-exist workspace directory.  Idempotent.
    """
    workspace_dir.mkdir(parents=True, exist_ok=True)

    # Canonical subdirectories
    for sub in ("skills", "memories", "logs", "run"):
        (workspace_dir / sub).mkdir(parents=True, exist_ok=True)

    # Seed SOUL.md from the bundled default if missing
    _ensure_soul(workspace_dir)

    return workspace_dir


def _ensure_soul(workspace_dir: Path) -> None:
    """Copy the bundled default SOUL.md into the workspace if absent."""
    soul = workspace_dir / "SOUL.md"
    if soul.exists():
        return
    import logging
    _log = logging.getLogger("magi.startup.paths")
    bundled = Path(__file__).resolve().parent.parent / "prompts" / "soul.md"
    if bundled.is_file():
        soul.write_text(bundled.read_text(encoding="utf-8"), encoding="utf-8")
        _log.info("SOUL.md seeded from %s", bundled)
    else:
        _log.warning("bundled soul.md missing at %s; SOUL.md not created", bundled)


# ------------------------------------------------------------------
# skills / memories / SOUL (workspace subdirectories)
# ------------------------------------------------------------------

def resolve_skills_dir(workspace_dir: Path) -> Path:
    return workspace_dir / "skills"


def resolve_memories_dir(workspace_dir: Path) -> Path:
    return workspace_dir / "memories"


def resolve_soul_path(workspace_dir: Path) -> Path:
    return workspace_dir / "SOUL.md"


# ------------------------------------------------------------------
# public API
# ------------------------------------------------------------------

__all__ = [
    # host
    "resolve_host_workspace",
    # directory bootstrapping
    "ensure_host_workspace",
    "ensure_workspace",
    # MAGI workspace
    "resolve_magi_workspace",
    # databases
    "resolve_magis_database_path",
    "resolve_private_database_path",
    "resolve_private_database_url",
    "resolve_magis_database_url",
    # runtime state
    "resolve_runtime_state_path",
    "resolve_runtime_pid_path",
    "resolve_runtime_log_paths",
    # WebUI
    "resolve_webui_pid_path",
    "resolve_webui_log_paths",
    # subdirectories
    "resolve_skills_dir",
    "resolve_memories_dir",
    "resolve_soul_path",
]
