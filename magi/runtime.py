"""Process composition for MAGI workers.

This module is intentionally outside ``agent``, ``tools``, ``channels``,
``connectors`` and ``plugins``.  It is the composition root's lifecycle adapter:
it knows concrete workers and cross-subsystem wiring, but carries no domain
logic.  Channel applications attach it as an ASGI lifespan.
"""

from __future__ import annotations

from contextlib import asynccontextmanager


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
                hook=Hook.ON_CONNECTOR_EVENT,
                connector=getattr(event, "connector", None),
                connector_event=event,
            )
            plugin_bus.emit(Hook.ON_CONNECTOR_EVENT, context)  # type: ignore[union-attr]
        except Exception:
            import logging
            logging.getLogger("magi.runtime").exception(
                "connector→plugin bridge forward failed"
            )

    for kind in ConnectorEventKind:
        connector_bus.subscribe(kind.value, _forward)
        _BRIDGE_HANDLERS.append((kind, _forward))

    import logging
    logging.getLogger("magi.runtime").info(
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
