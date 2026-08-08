"""Startup path resolution — every filesystem path needed by MAGI startup.

Per plan §6 / §9, the on-disk layout below is the single source of
truth — there is no longer a launcher package.  Most helpers take
the relevant inputs explicitly (no env reads, no side effects), so
tests can inject tmp paths directly.

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


# Legacy launcher compatibility — plan §20.1 migrates the launcher
# `state_dir()` / `workspace_dir()` zero-arg resolvers into the unified
# :func:`resolve_state_dir` / :func:`resolve_workspace_dir` defined below.
# These names read the same env vars the launcher did; semantics stay
# identical until the legacy callers are retired.


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

    .. note::

        ``SOUL.md`` seeding has moved to
        :func:`magi.new_bus.bootstrap.bootstrap_new_bus` — the
        composition root that owns prompt-file lifecycle.

    Returns the guaranteed-to-exist workspace directory.  Idempotent.
    """
    workspace_dir.mkdir(parents=True, exist_ok=True)

    # Canonical subdirectories
    for sub in ("skills", "memories", "logs", "run"):
        (workspace_dir / sub).mkdir(parents=True, exist_ok=True)

    return workspace_dir




# ------------------------------------------------------------------
# skills / memories / SOUL (workspace subdirectories)
# ------------------------------------------------------------------

def resolve_skills_dir(workspace_dir: Path) -> Path:
    return workspace_dir / "skills"


def resolve_memories_dir(workspace_dir: Path) -> Path:
    return workspace_dir / "memories"


def resolve_state_dir(
    host_workspace_dir: Path | None = None,
    magi_name: str | None = None,
) -> Path:
    """Return the canonical state directory for BUS SQLite + migrations.

    Plan §9 — ``magi.db`` lives directly under the MAGI workspace:
    ``<host>/MAGI_Citizens/<name>/magi.db``.

    Two calling conventions are supported:

    - ``resolve_state_dir(host, name)`` — explicit, the canonical
      composition-root path (no env reads).
    - ``resolve_state_dir()`` — launcher compatibility zero-arg form;
      reads ``HOST_WORKSPACE_DIR`` / ``MAGI_NAME`` / ``MAGI_WORKSPACE_DIR``
      exactly as the legacy :func:`magi.launcher.paths.state_dir` did.
    """
    import os

    # Explicit args win — no env reads.
    if host_workspace_dir is not None:
        if magi_name:
            return host_workspace_dir / "MAGI_Citizens" / magi_name / "workspace" / "memories"
        return host_workspace_dir / "MAGI_Societies" / "genesis-01" / "launcher-state"

    # Zero-arg launcher-compat branch.
    data_root = os.environ.get("HOST_WORKSPACE_DIR")
    if data_root:
        runtime_slug = os.environ.get("MAGI_NAME")
        if runtime_slug:
            return (
                Path(data_root).expanduser().resolve()
                / "MAGI_Citizens"
                / runtime_slug
                / "workspace"
                / "memories"
            )
        return Path(data_root).expanduser().resolve() / "MAGI_Societies" / "genesis-01" / "launcher-state"

    # K8s profile — no HOST_WORKSPACE_DIR set.
    raw_ws = os.environ.get("MAGI_WORKSPACE_DIR")
    if raw_ws:
        return Path(raw_ws) / "memories"
    return Path.home() / ".magi" / "MAGI_Citizens" / (os.environ.get("MAGI_NAME", "eva-000")) / "workspace" / "memories"


def resolve_workspace_dir() -> Path:
    """Return the operator's persistent workspace root (zero-arg variant).

    Mirror of the legacy :func:`magi.launcher.paths.workspace_dir` zero-arg
    resolver.  Reads ``MAGI_WORKSPACE_DIR`` / ``HOST_WORKSPACE_DIR`` /
    ``MAGI_NAME`` in priority order; raises if none are set.
    """
    import os as _os

    raw = _os.environ.get("MAGI_WORKSPACE_DIR")
    if raw:
        return Path(raw).expanduser().resolve()
    data_root = _os.environ.get("HOST_WORKSPACE_DIR")
    if data_root:
        runtime_slug = _os.environ.get("MAGI_NAME")
        if runtime_slug:
            return Path(data_root) / "MAGI_Citizens" / runtime_slug / "workspace"
        return (
            Path(data_root).expanduser().resolve()
            / "MAGI_Societies"
            / "genesis-01"
            / "launcher-workspace"
        )
    raise RuntimeError(
        "resolve_workspace_dir() needs MAGI_WORKSPACE_DIR or HOST_WORKSPACE_DIR"
    )


def bootstrap_workspace(workspace: Path) -> dict[str, str]:
    """Idempotent workspace bootstrap (alias for :func:`ensure_workspace`).

    Plan §20.1 retired ``magi.launcher.cli``; this helper now lives here
    for compatibility with any deployment script that still references
    it by name.  New code should call :func:`ensure_workspace` directly.

    SOUL.md seeding has moved to :func:`magi.new_bus.bootstrap.bootstrap_new_bus`.
    """
    workspace.mkdir(parents=True, exist_ok=True)
    created: dict[str, str] = {"workspace_root": "kept"}
    for sub, label in (
        ("skills", "skills/"),
        ("memories", "memories/"),
    ):
        target = workspace / sub
        target.mkdir(parents=True, exist_ok=True)
        created[label] = "kept"
    return created


def resolve_soul_path(workspace_dir: Path) -> Path:
    return workspace_dir / "SOUL.md"


# ------------------------------------------------------------------
# public API
# ------------------------------------------------------------------

__all__ = [
    # host
    "resolve_host_workspace",
    "resolve_state_dir",
    "resolve_workspace_dir",
    # directory bootstrapping
    "ensure_host_workspace",
    "ensure_workspace",
    "bootstrap_workspace",
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
