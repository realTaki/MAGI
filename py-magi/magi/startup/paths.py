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
    │   │   ├── memories/magi.db  # private SQLite
    │   │   ├── prompts/          # Worker-owned PromptBook files
    │   │   │   ├── agent/
    │   │   │   └── proactive/
    │   │   ├── skills/           # BUS-seeded SKILL.md directories
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

import os
import re
from pathlib import Path

# ------------------------------------------------------------------
# deployment mode detection
# ------------------------------------------------------------------

# Standard Kubernetes env var that every K8s Pod gets injected by the
# kubelet. Used here as the canonical "am I running inside a Pod?" probe.
_K8S_ENV_MARKER = "KUBERNETES_SERVICE_HOST"


def is_kubernetes_mode() -> bool:
    """Return ``True`` if this process is running inside a Kubernetes Pod.

    Detected via the standard ``KUBERNETES_SERVICE_HOST`` env var that the
    kubelet injects into every Pod. Inside a Pod the operator's "host"
    filesystem is the container's own root — K8s deployment owns the
    PVC mount path; the MAGI process only needs to know that the host
    root is ``/``.
    """
    return bool(os.environ.get(_K8S_ENV_MARKER))


# ------------------------------------------------------------------
# host workspace
# ------------------------------------------------------------------


def resolve_host_workspace() -> Path:
    """Return the default host workspace directory.

    Resolution order:

    1. ``HOST_WORKSPACE_DIR`` env var, if set (always wins, in either
       deployment mode).
    2. K8s mode (no env var, ``KUBERNETES_SERVICE_HOST`` is set): ``/``.
       From inside the container the "host" *is* the container — K8s
       owns the PVC mount path and the operator chooses where to mount
       the workspace data. MAGI derives its workspace from ``/``.
    3. CLI mode: ``$XDG_DATA_HOME/magi`` if set, else ``~/.magi``.

    This is the *only* function that reads the environment for host
    workspace resolution.
    """
    raw = os.environ.get("HOST_WORKSPACE_DIR")
    if raw:
        return Path(raw).expanduser().resolve()
    if is_kubernetes_mode():
        return Path("/")
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


def _magis_storage_name(magis_name: str) -> str:
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?", magis_name):
        raise ValueError(
            "MAGIS storage name must be a lowercase letter/digit slug with optional internal hyphens"
        )
    return magis_name


def resolve_magis_directory(host_workspace_dir: Path, magis_name: str = "genesis") -> Path:
    """Return the durable filesystem directory for one SQLite MAGIS."""
    return host_workspace_dir / "MAGI_Societies" / _magis_storage_name(magis_name)


def resolve_magis_database_path(host_workspace_dir: Path, magis_name: str = "genesis") -> Path:
    """Return the SQLite path for one named MAGIS.

    ``<host>/MAGI_Societies/<magis_name>/magis.db``.  This is deliberately
    distinct from a MAGI's private ``memories/magi.db``.
    """
    return resolve_magis_directory(host_workspace_dir, magis_name) / "magis.db"


def resolve_private_database_path(workspace_dir: Path) -> Path:
    """Return the private SQLite path for one MAGI.

    ``<workspace>/memories/magi.db``
    """
    return workspace_dir / "memories" / "magi.db"


def resolve_private_database_url(workspace_dir: Path) -> str:
    """Return a ``sqlite:///...`` URL for the private database."""
    db_path = resolve_private_database_path(workspace_dir)
    return f"sqlite:///{db_path}"


def resolve_magis_database_url(host_workspace_dir: Path, magis_name: str = "genesis") -> str:
    """Return the SQLite URL for one named MAGIS shared database."""
    db_path = resolve_magis_database_path(host_workspace_dir, magis_name)
    return f"sqlite:///{db_path}"


# ------------------------------------------------------------------
# runtime state
# ------------------------------------------------------------------


def resolve_runtime_state_path(workspace_dir: Path) -> Path:
    """Path to ``runtime.json`` — legacy per-workspace identity cache.

    Retained as a path resolver only; the runtime startup path no
    longer reads or writes this file.  Identity is now derived from
    the MAGIS shared database via :func:`magi.startup.spec.load_runtime_spec`.

    The legacy file (when present in an existing workspace) is
    ignored at startup and removed by the one-time cleanup hook in
    :func:`magi.startup.local.start_magi` once the new path is
    confirmed working.  Until then, the resolver stays so the
    cleanup can locate the file deterministically.
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
# skills / memories / SOUL (workspace subdirectories)
# ------------------------------------------------------------------


def resolve_skills_dir(workspace_dir: Path) -> Path:
    return workspace_dir / "skills"


def resolve_bundle_skills_dir() -> Path:
    """Return the path to the image-shipped skills bundle.

    Two-tier resolution, mirroring the prompts-bundle resolver:
    prefer anchoring on the ``magi`` package (normal installs and
    wheel / zip-app installs), fall back to deriving from
    ``__file__`` (when the package is not importable — e.g. ad-hoc
    test runs).
    """
    # Tier 1: ``magi/__init__.py`` is the canonical anchor for the
    # bundle, which is shipped inside the installed package.
    try:
        import magi

        candidate = Path(magi.__file__).resolve().parent / "skills"
        if candidate.is_dir():
            return candidate
    except Exception:
        pass

    # Tier 2: ``__file__`` fallback. This file lives at
    # ``magi/startup/paths.py``; three levels up is ``magi/``.
    # ``+ "skills"`` gives ``magi/skills/``.
    return Path(__file__).resolve().parents[2] / "skills"


def resolve_memories_dir(workspace_dir: Path) -> Path:
    return workspace_dir / "memories"


def resolve_state_dir(
    host_workspace_dir: Path | None = None,
    magi_name: str | None = None,
) -> Path:
    """Return the canonical state directory for bus SQLite + migrations.

    Per plan §9 — the bus SQLite file lives at
    ``<workspace>/memories/magi.db``; this resolver returns its parent:

        ``<HOST_WORKSPACE_DIR>/MAGI_Citizens/<MAGI_NAME>/memories``

    Two calling conventions are supported:

    - ``resolve_state_dir(host, name)`` — explicit, the canonical
      composition-root path (no env reads).
    - ``resolve_state_dir()`` — zero-arg, reads ``HOST_WORKSPACE_DIR`` /
      ``MAGI_NAME`` (with K8s vs CLI default applied via
      :func:`resolve_host_workspace`).

    The legacy ``MAGI_WORKSPACE_DIR`` env var is gone — workspace is
    always derived from host + name (plan §5 / §6). The legacy
    ``/workspace`` segment between ``<name>`` and ``memories`` is also
    gone; the on-disk layout now matches plan §9 exactly.
    """
    workspace = _resolve_workspace_root(host_workspace_dir, magi_name)
    return workspace / "memories"


def resolve_workspace_dir(
    host_workspace_dir: Path | None = None,
    magi_name: str | None = None,
) -> Path:
    """Return the canonical per-MAGI workspace root.

    Same calling conventions as :func:`resolve_state_dir` (explicit or
    zero-arg).  Result:

        ``<HOST_WORKSPACE_DIR>/MAGI_Citizens/<MAGI_NAME>``

    The legacy launcher returned an extra ``/workspace`` suffix here
    too — that has been dropped to match the layout in plan §9.  The
    zero-arg form raises if ``HOST_WORKSPACE_DIR`` is unset *and* K8s
    detection fails (i.e. plain local CLI with no env at all) — the
    default is ``~/.magi`` so this branch is only reachable when
    someone has stripped the home directory; it's a programmer error.
    """
    return _resolve_workspace_root(host_workspace_dir, magi_name)


def _resolve_workspace_root(
    host_workspace_dir: Path | None,
    magi_name: str | None,
) -> Path:
    """Shared resolver used by :func:`resolve_workspace_dir` and
    :func:`resolve_state_dir` — never reads environment when both
    args are supplied."""
    if host_workspace_dir is not None and magi_name is not None:
        return host_workspace_dir / "MAGI_Citizens" / magi_name
    # Zero-arg branch — env reads via the canonical helpers.
    if host_workspace_dir is None:
        host_workspace_dir = resolve_host_workspace()
    if magi_name is None:
        from magi.startup.config import DEFAULT_MAGI_NAME

        magi_name = os.environ.get("MAGI_NAME", DEFAULT_MAGI_NAME)
    return host_workspace_dir / "MAGI_Citizens" / magi_name


# ------------------------------------------------------------------
# public API
# ------------------------------------------------------------------

__all__ = [
    # mode detection
    "is_kubernetes_mode",
    # host
    "resolve_host_workspace",
    "resolve_state_dir",
    "resolve_workspace_dir",
    # MAGI workspace
    "resolve_magi_workspace",
    # databases
    "resolve_magis_database_path",
    "resolve_magis_directory",
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
    "resolve_bundle_skills_dir",
    "resolve_memories_dir",
]
