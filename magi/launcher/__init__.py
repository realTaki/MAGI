"""MAGI launcher — composition-root package for both deploy profiles.

This package is the single Composition-Root home for MAGI. It is the
only place that knows about path layouts, Local-vs-K8s bootstrap
selection, or process-lifecycle wiring for channels / workers / the
connector-to-plugin bridge.  Business modules (``magi.bus``,
``magi.agent``, ``magi.tools``, ``magi.channels``, ``magi.mcp``,
``magi.connectors``, ``magi.skills``, ``magi.proactive``,
``magi.orchestrator``) never import from here.

Layout:

- ``LocalPathLayout`` (here)             — path layout dataclass
- ``bootstrap_local`` (here)             — Local Profile Composition Root
- ``start_channel`` / ``stop_channel`` /
  ``is_channel_running`` / ``start_connector_bridge`` /
  ``stop_connector_bridge`` /
  ``worker_lifespan`` (here)             — process lifecycle adapter
- ``paths``                              — OS-specific data-root resolution
- ``platform``                           — OS detection + browser open
- ``security``                           — launcher control-secret helpers
- ``cli``                                — ``magi local start|status|stop|doctor|install-service|uninstall-service``

The architecture test (``tests/architecture/test_import_boundaries.py``)
treats this package as a Composition-Root prefix, exempt from the
standard bus-centric boundary rules.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

# §1. Path layout config --------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LocalPathLayout:
    """Filesystem layout for one MAGI runtime or the launcher process.

    Two modes, distinguished by whether ``runtime_id`` + ``slug`` are
    provided:

    **Runtime mode** (``runtime_id`` + ``slug`` supplied):
        ``<data_root>/MAGIC/<slug>/``
        ├── workspace/
        │   ├── memories/magi.db   (SQLite — via :func:`~magi.launcher.paths.state_dir`)
        │   ├── skills/
        │   ├── SOUL.md
        │   ├── logs/
        │   └── tmp/
        └── state/                 (SQLite — per-MAGI isolation)

    **Launcher mode** (no ``runtime_id``):
        ``<data_root>/control/launcher-state/`` — scratch space for the
        launcher's BUS services.  The launcher never runs agent work;
        the real runtime state lives in the subprocess's per-MAGI slot.

    Layout under ``data_root`` (runtime mode)::

        <data_root>/
        ├── control/
        │   ├── local-registry.db
        │   ├── control-secret
        │   ├── launcher.json
        │   └── launcher-state/magi.db       (launcher-only scratch)
        ├── MAGIC/<slug>/workspace/
        │   ├── SOUL.md
        │   ├── skills/
        │   ├── memories/magi.db              (SQLite — per-MAGI private)
        │   ├── logs/
        │   └── tmp/
        └── MAGIS/<magis-id>-<slug>/magis.db  (SQLite — per-MAGIS public)
    """

    data_root: Path
    runtime_id: int | None = None
    slug: str | None = None

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

        if self.runtime_id is not None and self.slug:
            # Runtime mode: per-MAGI slot under MAGIC/<slug>/workspace/
            # state_dir = workspace/memories — matches K8s convention.
            ws = data_root / "MAGIC" / self.slug / "workspace"
            st = ws / "memories"
            object.__setattr__(self, "state_dir", st)
            object.__setattr__(self, "workspace", ws)
            object.__setattr__(self, "local_db", st / "magi.db")
            object.__setattr__(self, "skills_dir", ws / "skills")
            object.__setattr__(self, "soul_path", ws / "SOUL.md")
            object.__setattr__(self, "logs_dir", ws / "logs")
            object.__setattr__(self, "temp_dir", ws / "tmp")
            object.__setattr__(self, "audit_log_path", ws / "logs" / "audit.log")
        else:
            # Launcher mode: scratch space in control/ so it never
            # collides with the Adam's per-MAGI magi.db.
            launcher_state = data_root / "control" / "launcher-state"
            object.__setattr__(self, "state_dir", launcher_state)
            object.__setattr__(self, "workspace", data_root)  # unused
            object.__setattr__(self, "local_db", launcher_state / "magi.db")
            object.__setattr__(self, "skills_dir", data_root / "skills")  # unused
            object.__setattr__(self, "soul_path", data_root / "SOUL.md")  # unused
            object.__setattr__(self, "logs_dir", data_root / "logs")  # unused
            object.__setattr__(self, "temp_dir", data_root / "tmp")  # unused
            object.__setattr__(self, "audit_log_path", data_root / "logs" / "audit.log")

        object.__setattr__(self, "magis_workspace", data_root / "MAGIS")


# §2. Local Composition Root ----------------------------------------------------


from magi.bus import Bus  # noqa: E402  (Composition Root — only path that uses _bootstrap)
from magi.bus.bootstrap import _bootstrap as _bus_bootstrap  # noqa: E402


def bootstrap_local(
    data_root: Path | str,
    *,
    initialise: bool = False,
    magis_dir_override: Path | str | None = None,
) -> Bus:
    """Build the Local Profile BUS facade rooted at ``data_root``.

    NOTE: this calls :func:`magi.bus.bootstrap` (not
    :func:`magi.bus.get_bus`) — the Local Profile needs a
    composition that owns the chosen ``state_dir`` and ``magis_engine``,
    which the process-wide singleton does not.

    ``data_root`` becomes the root of the :class:`LocalPathLayout`.

    All downstream workers receive their ``state_dir`` from this layout via
    the BUS facade — no business module reaches back to the layout
    itself.

    When ``initialise=True`` the function bootstraps the on-disk SQLite
    schema (idempotent — safe to call on every launch).  ``magi local
    start`` is the canonical caller; tests may pass ``initialise=True``
    to set up a fresh ``tmp_path`` fixture.

    ``magis_dir_override`` overrides the per-MAGIS SQLite location; when
    ``None`` the function picks ``<data_root>/MAGIS/1-genesis/`` (the
    first MAGIS seeded by the Local Profile is always Genesis with id=1).
    This matches the K8s pattern ``MAGIS/<magis_id>-<slug>/magis.db`` so
    the host layout is identical across both profiles.

    The control-plane runtime registry (``bus.control_registry``) is
    built on the same MAGIS engine — no separate ``control/`` database.
    """
    layout = LocalPathLayout(Path(data_root))
    if magis_dir_override is None:
        from magi.launcher.paths import magis_dir as _magis_dir

        magis_dir = _magis_dir(Path(data_root), 1, "genesis")
    else:
        magis_dir = Path(magis_dir_override)
    magis_dir.mkdir(parents=True, exist_ok=True)

    from magi.bus.db.magis.local_engine import build as build_local_engine

    magis_engine = build_local_engine(magis_dir)

    return _bus_bootstrap(
        str(layout.state_dir),
        initialise_local=initialise,
        magis_engine=magis_engine,
    )


# §3. Process lifecycle wiring --------------------------------------------------


def start_channel(name: str) -> None:
    """Start a concrete channel from the composition layer."""
    if name == "telegram":
        from magi.channels.telegram.bot import start_bot

        start_bot()


def stop_channel(name: str) -> None:
    if name == "telegram":
        from magi.channels.telegram.bot import stop_bot

        stop_bot()


def is_channel_running(name: str) -> bool:
    if name == "telegram":
        from magi.channels.telegram.bot import is_running

        return is_running()
    return name == "webui"


# -- connector→plugin bridge (composition wiring) ------------------------------

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
                hook=Hook.ON_CONNECTOR_EVANT,
                connector=getattr(event, "connector", None),
                connector_event=event,
            )
            plugin_bus.emit(Hook.ON_CONNECTOR_EVANT, context)  # type: ignore[union-attr]
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
]
