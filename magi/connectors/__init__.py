"""Connectors — bridges between MAGI and external data sources.

DESIGN (this docstring is the canonical design reference; everything
else in this package is scaffolding around it).

Why a new subsystem
-------------------
MAGI already has three ways to talk to the outside world:

  - **Channels** (``magi/channels/``) — bidirectional conversation
    surfaces. A human (or peer MAGI over a2a) sends a message; MAGI
    replies. Examples: Telegram bot, WebUI console, a2a peer.

  - **Tools** (``magi/tools/``) — synchronous functions the LLM calls
    during a turn. Examples: ``read_file``, ``edit_file``, MCP
    server-discovered tools.

  - **Skills** (``magi/skills/*/SKILL.md``) — soft markdown injected
    into the system prompt. The LLM reads them but doesn't call them.

None of these three naturally fits **connectors**: connectors are
about *streams of external events and data* that MAGI consumes on
its own schedule, not on the LLM's turn-by-turn cadence. Examples:

  - Gmail — new mail arrives every few minutes, not when the LLM
    asks.
  - Google Calendar — event reminders fire on time.
  - Linear / Notion — issue status changes.
  - Webhook subscribers — incoming HTTP.

A connector is a long-lived object with three responsibilities:

  1. **Connect** at boot — open the upstream socket, fetch the
     initial state, register for push notifications.
  2. **Emit events** into the connector event bus whenever
     upstream state changes (or a periodic poll observes a delta).
  3. **Serve fetches** when the LLM asks "what's on my calendar
     today?" via a tool wrapper — this is how the connector
     reaches the LLM, by exposing its query surface as tools.

The LLM never calls a connector directly. It calls the tools that
*wrap* a connector — same pattern as MCP-discovered tools.

Connector vs channel
--------------------
Channels are *conversational*: every inbound message has a
``uid`` (the human or peer who's talking), a session, a reply path.
Connectors are *observational*: every event is a timestamped data
record. A connector doesn't know how to reply to a message; it
only knows how to fetch + emit.

Connector vs tool
-----------------
Tools are *synchronous* and *call-driven*; the LLM decides when
to fire them. Connector events are *asynchronous* and *push-driven*;
upstream decides when they fire. The connector exposes its query
surface via ``fetch()`` which tool wrappers call — but the
connector itself is not a tool.

Lifecycle
---------

  1. Operator adds a :class:`ConnectorConfig` to the private SQLite
     ``connector_configs`` table (WebUI → Settings → Connectors,
     or via ``POST /api/connectors``).
  2. Runtime boots → ``load_connectors()`` reads enabled configs →
     constructs one :class:`Connector` per config → calls
     ``await connector.connect()``.
  3. The connector subscribes to upstream, emits events into
     the bus. Domain code subscribes via ``bus.subscribe(kind, handler)``.
  4. On shutdown, ``await connector.disconnect()`` flushes state
     and closes upstream sockets.

Event bus
---------
The bus lives at module level (``_EVENT_BUS``). Subscribers are
coroutines that take a :class:`ConnectorEvent` and return ``None``.
The bus is in-process only — no cross-process pub/sub. Cross-MAGI
event sharing would route through a2a (deferred).

Hook vs plugin
--------------
Plugins (``magi/plugins/``) observe what MAGI *does* — tool
calls, channel sends, LLM calls. Connectors observe what the
*outside world* does. Same event-bus primitive; orthogonal
producers.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# DISABLED — ``magi.connectors`` is commented out. The BUS
# (``magi.bus``) has not yet integrated the connector lifecycle /
# event-bus primitives this package depends on, and the package is
# blocked on a few upstream decisions (where connector configs live,
# how events cross MAGI boundaries, tool-wrapper auto-derivation).
# Until those decisions land, importing this package would race with
# partial wiring.
#
# The historical contracts (Connector / ConnectorConfig / ConnectorEvent /
# EventBus / registry) are kept verbatim below so they can be
# re-enabled in one move once the integration plan is settled. To
# re-enable: uncomment the import block and the ``__all__`` list, then
# delete this banner.
#
# Known fallout while disabled:
#   - ``tests/unit/test_connectors.py`` will fail to import. Re-enable
#     the package, or comment that test file out, before running the
#     unit suite.
# ---------------------------------------------------------------------------

# from magi.connectors.base import (
#     Connector,
#     ConnectorConfig,
#     ConnectorEvent,
#     ConnectorEventKind,
#     EventHandler,
# )
# from magi.connectors.bus import (
#     EventBus,
#     get_bus,
#     publish,
#     reset_bus,
#     subscribe,
# )
# from magi.connectors.registry import (
#     get_connector,
#     get_factory,
#     list_connectors,
#     load_connectors,
#     register_connector,
#     register_connector_factory,
#     reset as reset_registry,
#     unload_all,
# )
#
# __all__ = [
#     # Protocol types
#     "Connector",
#     "ConnectorConfig",
#     "ConnectorEvent",
#     "ConnectorEventKind",
#     "EventHandler",
#     # Bus API
#     "EventBus",
#     "get_bus",
#     "publish",
#     "reset_bus",
#     "subscribe",
#     # Registry API
#     "get_connector",
#     "get_factory",
#     "list_connectors",
#     "load_connectors",
#     "register_connector",
#     "register_connector_factory",
#     "reset_registry",
#     "unload_all",
# ]

__all__: list[str] = []