"""MAGI launcher — composition-root module for both deploy profiles.

This module is the single Composition-Root home for MAGI today. It is
the only place that knows about path layouts, Local-vs-K8s bootstrap
selection, or process-lifecycle wiring for channels / workers / the
connector-to-plugin bridge. Business modules (``magi.bus``,
``magi.agent``, ``magi.tools``, ``magi.channels``, ``magi.mcp``,
``magi.connectors``, ``magi.skills``, ``magi.proactive``,
``magi.orchestrator``) never import from here.

Sectioned by concern for readability, but kept as one flat file:

  §1. Path layout config       — ``LocalPathLayout`` dataclass
  §2. Local Composition Root   — ``bootstrap_local()``
  §3. Process lifecycle wiring — channel/bridge/worker adapter
  §4. Back-compat re-export    — ``workspace_root`` (canonical: ``magi.agent.workspace``)

The architecture test (``tests/architecture/test_import_boundaries.py``)
treats this module as a Composition-Root prefix, exempt from the standard
bus-centric boundary rules.  When Phase 6 lands
(``magi local start|status|stop|doctor`` per
``docs/MAGI_LOCAL_STANDALONE_DEPLOYMENT_IMPLEMENTATION_PLAN.md`` §13.2),
convert this module into the ``magi/launcher/`` package and add
``cli.py`` / ``supervisor.py`` / ``paths.py`` / ``ports.py`` /
``platform.py`` / ``security.py`` per the plan.
"""

from __future__ import annotations

# §1. Path layout config --------------------------------------------------------

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class LocalPathLayout:
    """Filesystem layout for one MAGI runtime.

    Single required argument: ``data_root``.  All other paths are derived
    inside :meth:`__post_init__`.  No env-var reads happen here — the
    Composition Root is the only place that decides which layout to build.

    Layout under ``data_root``::

        <data_root>/
        ├── control/
        │   ├── local-registry.db
        │   ├── control-secret
        │   ├── launcher.json
        │   └── logs/
        ├── MAGIC/<runtime-id>-<slug>/workspace/
        │   ├── memories/magi.db
        │   ├── skills/
        │   ├── SOUL.md
        │   ├── logs/
        │   └── tmp/
        └── MAGIS/<magis-id>-<slug>/magis.db
    """

    data_root: Path

    # Derived (post_init)
    state_dir: Path = None  # type: ignore[assignment]
    workspace: Path = None  # type: ignore[assignment]
    local_db: Path = None  # type: ignore[assignment]
    skills_dir: Path = None  # type: ignore[assignment]
    soul_path: Path = None  # type: ignore[assignment]
    logs_dir: Path = None  # type: ignore[assignment]
    temp_dir: Path = None  # type: ignore[assignment]
    magis_workspace: Path = None  # type: ignore[assignment]
    audit_log_path: Path = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        # Use object.__setattr__ because the dataclass is frozen.
        data_root = Path(self.data_root).expanduser().resolve()
        object.__setattr__(self, "data_root", data_root)
        object.__setattr__(self, "state_dir", data_root / "state")
        object.__setattr__(self, "workspace", data_root / "workspace")
        object.__setattr__(self, "local_db", self.state_dir / "magi.db")
        object.__setattr__(self, "skills_dir", self.workspace / "skills")
        object.__setattr__(self, "soul_path", self.workspace / "SOUL.md")
        object.__setattr__(self, "logs_dir", self.workspace / "logs")
        object.__setattr__(self, "temp_dir", self.workspace / "tmp")
        object.__setattr__(self, "magis_workspace", data_root / "MAGIS")
        object.__setattr__(self, "audit_log_path", data_root / "logs" / "audit.log")


# §2. Local Composition Root ----------------------------------------------------

from magi.bus import Bus, bootstrap  # noqa: E402  (kept after config above so §1 reads cleanly)


def bootstrap_local(
    data_root: Path | str,
    *,
    initialise: bool = False,
    magis_dir: Path | str | None = None,
) -> Bus:
    """Build the Local Profile BUS facade rooted at ``data_root``.

    ``data_root`` becomes the root of the :class:`LocalPathLayout`.  All
    downstream workers receive their ``state_dir`` from this layout via
    the BUS facade — no business module reaches back to the layout
    itself.

    When ``initialise=True`` the function bootstraps the on-disk SQLite
    schema (idempotent — safe to call on every launch).  Phase 6's
    ``magi local start`` launcher is the canonical caller; tests may pass
    ``initialise=True`` to set up a fresh ``tmp_path`` fixture.

    ``magis_dir`` (Phase 3) overrides the per-MAGIS SQLite location; when
    ``None`` the function picks ``<data_root>/MAGIS/local/magis.db``.  The
    resulting engine is injected into the BUS so the public schema lives
    outside the Adam's private database.
    """
    layout = LocalPathLayout(Path(data_root))
    if magis_dir is None:
        magis_dir = Path(data_root).expanduser().resolve() / "MAGIS" / "local"
    magis_dir = Path(magis_dir)
    magis_dir.mkdir(parents=True, exist_ok=True)

    # Phase 3 — build the per-MAGIS SQLite engine eagerly so the BUS
    # facade receives it via bootstrap(..., magis_engine=...).
    from magi.bus.db.magis.local_engine import build as build_local_engine

    magis_engine = build_local_engine(magis_dir)
    return bootstrap(
        str(layout.state_dir),
        initialise_local=initialise,
        magis_engine=magis_engine,
    )


# §3. Process lifecycle wiring --------------------------------------------------

from contextlib import asynccontextmanager  # noqa: E402


def start_channel(name: str, state_dir: str) -> None:
    """Start a concrete channel from the composition layer."""
    if name == "telegram":
        from magi.channels.telegram.bot import start_bot

        start_bot(state_dir)


def stop_channel(name: str) -> None:
    if name == "telegram":
        from magi.channels.telegram.bot import stop_bot

        stop_bot()


def is_channel_running(name: str) -> bool:
    if name == "telegram":
        from magi.channels.telegram.bot import is_running

        return is_running()
    return name == "webui"


# -- connector→plugin bridge (composition wiring) -------------------------------

_BRIDGE_HANDLERS: list[tuple] = []


def start_connector_bridge(plugin_bus: object) -> None:
    """Wire connector events into the plugin bus.

    Connectors emit *external* events (Gmail push, Calendar reminder);
    plugins observe *internal* events (tool calls, channel sends).  This
    bridge lets the audit_log plugin record connector events alongside
    tool calls without re-implementing the subscription.

    Lives here — not in ``magi.connectors`` — because it depends on both
    subsystems: it is composition wiring, not connector domain logic.
    """
    stop_connector_bridge()

    from magi.connectors.base import ConnectorEventKind
    from magi.connectors.bus import get_bus as get_connector_bus
    from magi.plugins.base import Hook, PluginContext

    connector_bus = get_connector_bus()

    async def _forward(event: object) -> None:
        try:
            context = PluginContext(
                hook=Hook.ON_CONNECTOR_EVENT,
                connector=getattr(event, "connector", None),
                connector_event=event,
            )
            plugin_bus.emit(Hook.ON_CONNECTOR_EVENT, context)  # type: ignore[union-attr]
        except Exception:
            import logging

            logging.getLogger("magi.launcher").exception(
                "connector→plugin bridge forward failed"
            )

    for kind in ConnectorEventKind:
        connector_bus.subscribe(kind.value, _forward)
        _BRIDGE_HANDLERS.append((kind, _forward))

    import logging

    logging.getLogger("magi.launcher").info(
        "connector→plugin bridge started: kinds=%d", len(_BRIDGE_HANDLERS),
    )


def stop_connector_bridge() -> None:
    """Drop connector bus subscriptions (test + atexit)."""
    global _BRIDGE_HANDLERS
    if not _BRIDGE_HANDLERS:
        return
    try:
        from magi.connectors.bus import get_bus as get_connector_bus

        connector_bus = get_connector_bus()
        for kind, handler in _BRIDGE_HANDLERS:
            connector_bus.unsubscribe(kind.value, handler)
    except Exception:
        pass
    _BRIDGE_HANDLERS = []


@asynccontextmanager
async def worker_lifespan():
    """Run local durable workers for the lifetime of an ASGI process."""
    from magi.agent.worker import start_agent_worker, stop_agent_worker
    from magi.channels.delivery import start_delivery_worker, stop_delivery_worker
    from magi.tools.worker import start_tool_worker, stop_tool_worker

    await start_agent_worker()
    await start_tool_worker()
    await start_delivery_worker()
    try:
        yield
    finally:
        await stop_delivery_worker()
        await stop_tool_worker()
        await stop_agent_worker()


# §4. Back-compat re-export -----------------------------------------------------

# The canonical helper lives in ``magi.agent.workspace``.  Re-exported here
# so legacy callers that imported ``from magi.workspace import
# workspace_root`` (or ``from magi.launcher import workspace_root`` after
# the consolidation) keep working.
from magi.agent.workspace import workspace_root  # noqa: E402, F401


__all__ = [
    # §1
    "LocalPathLayout",
    # §2
    "bootstrap_local",
    # §3
    "start_channel",
    "stop_channel",
    "is_channel_running",
    "start_connector_bridge",
    "stop_connector_bridge",
    "worker_lifespan",
    # §4
    "workspace_root",
]
