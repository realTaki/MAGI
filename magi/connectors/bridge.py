"""Connector→plugin bus bridge.

Forwards every :class:`ConnectorEvent` published on the
connector bus to the plugin bus as an
``on_connector_event`` :class:`PluginContext`. The bridge
is the runtime's wiring between the two subsystems:

  - **connectors** emit *external* events (Gmail push,
    Calendar reminder) via the connector bus.
  - **plugins** observe *internal* events (tool call,
    channel send) via the plugin bus.

The bridge lets the audit_log plugin (for example) record
connector events alongside tool calls without each plugin
re-implementing the same subscription.

The bridge is a singleton — one set of ``subscribe`` /
``unsubscribe`` calls per process. Call
:func:`start_connector_bridge` once at boot; call
:func:`stop_connector_bridge` at shutdown (atexit handles
this in the runtime boot path).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from magi.connectors.base import ConnectorEvent, ConnectorEventKind
    from magi.plugins.base import HookBus

logger = logging.getLogger("magi.connectors.bridge")

# Tracks the handler we registered against each kind so
# :func:`stop_connector_bridge` can unsubscribe cleanly.
_BRIDGE_HANDLERS: list[tuple["ConnectorEventKind", Any]] = []


async def _forward(plugin_bus: "HookBus", event: "ConnectorEvent") -> None:
    """Translate one :class:`ConnectorEvent` into a
    :class:`PluginContext` and emit it on the plugin bus.

    The translation is best-effort — fields the plugin
    bus doesn't understand (``connector_event``) are
    attached as an opaque attribute. Plugins that care
    inspect ``context.connector_event`` directly.
    """
    from magi.plugins.base import Hook, PluginContext

    context = PluginContext(
        hook=Hook.ON_CONNECTOR_EVENT,
        connector=getattr(event, "connector", None),
        connector_event=event,
    )
    plugin_bus.emit(Hook.ON_CONNECTOR_EVENT, context)


def start_connector_bridge(plugin_bus: "HookBus") -> None:
    """Subscribe to every connector event kind and
    forward to ``plugin_bus``.

    Idempotent — calling twice replaces the prior
    subscription set (the connector bus singleton keeps
    the latest handlers).
    """
    stop_connector_bridge()

    from magi.connectors.base import ConnectorEventKind
    from magi.connectors.bus import get_bus as get_connector_bus

    connector_bus = get_connector_bus()

    async def handler(event: "ConnectorEvent") -> None:
        try:
            await _forward(plugin_bus, event)
        except Exception:
            logger.exception("connector→plugin bridge forward failed")

    # Subscribe once per kind so the bridge sees every
    # event. A future ConnectorEventKind value will be
    # picked up automatically when a publisher emits it
    # only if a subscriber is already registered — for
    # now we enumerate the enum at boot time.
    for kind in ConnectorEventKind:
        connector_bus.subscribe(kind.value, handler)
        _BRIDGE_HANDLERS.append((kind, handler))

    logger.info(
        "connector→plugin bridge started: kinds=%d", len(_BRIDGE_HANDLERS),
    )


def stop_connector_bridge() -> None:
    """Drop the connector bus subscriptions (test + atexit)."""
    global _BRIDGE_HANDLERS
    if not _BRIDGE_HANDLERS:
        return
    try:
        from magi.connectors.bus import get_bus as get_connector_bus
        connector_bus = get_connector_bus()
        for kind, handler in _BRIDGE_HANDLERS:
            connector_bus.unsubscribe(kind.value, handler)
    except Exception:  # noqa: BLE001
        pass
    _BRIDGE_HANDLERS = []


__all__ = [
    "start_connector_bridge",
    "stop_connector_bridge",
]