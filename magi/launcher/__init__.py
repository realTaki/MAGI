"""Deprecated compatibility shim — plan §20.1.

``magi.launcher`` is being phased out in favour of
:mod:`magi.startup`. Every name that previously lived in this package
is now re-exported from :mod:`magi.startup` so existing callers keep
working until they're migrated.

New code should import directly from :mod:`magi.startup.*`.
"""

from __future__ import annotations

# Local compatibility — paths / constants.
from magi.launcher import constants as _constants  # noqa: F401

# Re-export path helpers. New code should use ``magi.startup.paths``
# directly.
from magi.startup.paths import (  # noqa: F401
    ensure_host_workspace,
    ensure_workspace,
    resolve_host_workspace as default_data_root,
    resolve_magi_workspace as workspace_dir,
    resolve_runtime_pid_path,
    resolve_runtime_log_paths,
    resolve_runtime_state_path,
    resolve_magis_database_path as magis_db_path,
    resolve_magis_database_url,
    resolve_private_database_path,
    resolve_webui_pid_path,
    resolve_webui_log_paths,
)

# Path-layout compatibility shim — kept for any code that referenced
# ``magi.launcher.LocalPathLayout``. The new layout is the single
# ``magi.startup.config.StartupConfig`` dataclass; legacy callers
# should migrate.
from dataclasses import dataclass
from pathlib import Path as _Path


@dataclass(frozen=True, slots=True)
class LocalPathLayout:  # noqa: F401 — legacy compatibility
    """Deprecated — use :class:`magi.startup.config.StartupConfig`."""

    data_root: _Path
    runtime_id: int | None = None
    slug: str | None = None
    state_dir: _Path | None = None
    workspace: _Path | None = None
    local_db: _Path | None = None
    skills_dir: _Path | None = None
    soul_path: _Path | None = None
    logs_dir: _Path | None = None
    temp_dir: _Path | None = None
    magis_workspace: _Path | None = None
    audit_log_path: _Path | None = None

    def __post_init__(self) -> None:  # pragma: no cover
        object.__setattr__(self, "data_root", _Path(self.data_root).expanduser().resolve())
        if self.runtime_id is not None and self.slug:
            ws = self.data_root / "MAGI_Citizens" / self.slug
            st = ws / "memories"
            object.__setattr__(self, "state_dir", st)
            object.__setattr__(self, "workspace", ws)
            object.__setattr__(self, "local_db", st / "magi.db")
            object.__setattr__(self, "skills_dir", ws / "skills")
            object.__setattr__(self, "soul_path", ws / "SOUL.md")
            object.__setattr__(self, "logs_dir", ws / "logs")
            object.__setattr__(self, "temp_dir", ws / "tmp")
            object.__setattr__(self, "audit_log_path", ws / "logs" / "audit.log")
        object.__setattr__(
            self,
            "magis_workspace",
            self.data_root / "MAGI_Societies",
        )


# ------------------------------------------------------------------
# bootstrap_local — superseded by magi.startup.bootstrap.bootstrap_magi.
# ------------------------------------------------------------------
def bootstrap_local(data_root, *, initialise: bool = False, magis_dir_override=None):  # noqa: F401
    """Deprecated — use :func:`magi.startup.bootstrap.bootstrap_magi`."""
    from magi.bus.bootstrap import bootstrap as _bus_bootstrap
    from magi.startup.config import StartupConfig

    cfg = StartupConfig(host_workspace_dir=_Path(data_root), magi_name="eva-000",
                       magis_database_url=None, magi_id=None)
    # Bootstrap + initialise the local SQLite.
    _bus_bootstrap(initialise_local=initialise)
    return _bus_bootstrap()


# ------------------------------------------------------------------
# Channel lifecycle shims (Telegram).
# ------------------------------------------------------------------
def start_channel(name: str) -> None:  # noqa: F401
    if name == "telegram":
        from magi.channels.telegram.bot import start_bot
        start_bot()


def stop_channel(name: str) -> None:  # noqa: F401
    if name == "telegram":
        from magi.channels.telegram.bot import stop_bot
        stop_bot()


def is_channel_running(name: str) -> bool:  # noqa: F401
    if name == "telegram":
        from magi.channels.telegram.bot import is_running
        return is_running()
    return name == "webui"


# ------------------------------------------------------------------
# worker_lifespan — moved into magi.startup.runtime.
# ------------------------------------------------------------------
def worker_lifespan():  # noqa: F401
    """Deprecated — use :func:`magi.startup.runtime`'s lifespan."""
    from magi.startup.runtime import _runtime_lifespan, WorkerHandles
    return _runtime_lifespan(WorkerHandles(), [])


# Constants kept for code that reads them directly.
MAGIC_DIR_NAME = "MAGI_Citizens"
MAGIS_DIR_NAME = "MAGI_Societies"


__all__ = [
    "LocalPathLayout",
    "bootstrap_local",
    "start_channel",
    "stop_channel",
    "is_channel_running",
    "worker_lifespan",
    "MAGIC_DIR_NAME",
    "MAGIS_DIR_NAME",
    "default_data_root",
    "workspace_dir",
    "magis_db_path",
    "magis_dir",  # legacy alias
    "state_dir",  # legacy alias
    "ensure_workspace",
    "ensure_host_workspace",
]


def magis_dir(data_root, magis_id=1, slug="genesis"):  # noqa: F401
    return _Path(data_root) / "MAGI_Societies" / f"{slug}-{magis_id:02d}"


def state_dir() -> _Path:  # noqa: F401
    """Legacy alias for the per-MAGI state directory."""
    from magi.startup.paths import resolve_state_dir as _resolve_state_dir
    return _resolve_state_dir()


def bootstrap_workspace(workspace: _Path):  # noqa: F401
    """Deprecated — use :func:`magi.startup.paths.ensure_workspace`."""
    from magi.startup.paths import ensure_workspace as _ensure
    _ensure(_Path(workspace))
    # skills/ bootstrap is a no-op for the new layout.
    return {"workspace_root": "kept"}


def magis_home(data_root: _Path) -> _Path:  # noqa: F401
    return magis_dir(data_root)


def control_secret_path(magis_home: _Path) -> _Path:  # noqa: F401
    return _Path(magis_home) / "control-secret"


def launcher_state_path(magis_home: _Path) -> _Path:  # noqa: F401
    return _Path(magis_home) / "launcher.json"