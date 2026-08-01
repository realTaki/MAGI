"""Plugins — cross-cutting hooks into the MAGI runtime.

DESIGN (this docstring is the canonical reference; everything
else in this package is scaffolding).

Why a new subsystem
-------------------
MAGI already has three extension points:

  - **Tools** (``magi/tools/``) — synchronous functions the LLM
    actively calls during a turn.

  - **Channels** (``magi/channels/``) — bidirectional IM /
    peer-message surfaces.

  - **Skills** (``magi/skills/*/SKILL.md``) — soft prompt
    fragments injected into the system prompt.

None of these three fits **plugins**: plugins observe
what MAGI *does* — every tool call, every channel send,
every LLM call — and react. Plugins don't add new abilities
the LLM can call; they add *behavior* that lives alongside
the LLM without the LLM's knowledge.

Examples:

  - **audit log** — every ``after_tool_call`` and
    ``after_channel_send`` is written to the operator-facing
    audit log table.
  - **cost counter** — every ``after_llm_call`` records
    token spend per operator.
  - **webhook fan-out** — every ``after_tool_call`` with a
    matching ``tool.name`` POSTs the result to an external URL.
  - **rate-limiter / rate-counter** — counts events per
    ``uid`` and refuses new ones over a threshold.

Plugins are *cross-cutting*: one plugin can subscribe to
multiple hooks. The audit_log plugin in
:mod:`magi.plugins.samples.audit_log` shows the pattern.

Hook catalog
------------
The :data:`Hook` enum is the single source of truth for
which hooks exist. Adding a new hook is a 3-step ritual:

  1. Add the value to :class:`Hook`.
  2. Call ``bus.hook.emit(Hook.NEW, context)`` at the
     appropriate call site in the runtime.
  3. Document the context payload in this docstring.

Existing hooks:

  - ``before_tool_call``    — before :meth:`Tool.run`
  - ``after_tool_call``     — after :meth:`Tool.run`
    completes (success OR error)
  - ``before_llm_call``     — before each LLM chat call
  - ``after_llm_call``      — after each LLM chat call
    (success OR error)
  - ``before_channel_send`` — before :func:`dispatcher.send_to_uid`
  - ``after_channel_send``  — after :func:`dispatcher.send_to_uid`
  - ``before_connector_fetch``  — before :meth:`Connector.fetch`
  - ``after_connector_fetch``   — after :meth:`Connector.fetch`
  - ``on_connector_event``  — fires for every :class:`ConnectorEvent`
    on the connector bus (forwarded via the plugin bus)
  - ``on_session_open``     — chat session opened
  - ``on_session_close``    — chat session closed

Plugin contract
---------------
A plugin is a small object:

  - :attr:`name` — stable id
  - :attr:`version` — semver-ish string, surfaces in logs
  - :meth:`register(bus)` — called once at boot; plugin
    subscribes to its hooks via ``bus.hook.subscribe(...)``
  - :meth:`shutdown()` — called at runtime shutdown

Plugins MUST NOT block. Heavy work goes through
``asyncio.create_task`` so the runtime's hot paths (tool
calls, LLM calls) stay fast.

Plugin vs connector
-------------------
Connectors emit *external* events (Gmail push, Calendar
reminder). Plugins observe *internal* events (tool call,
channel send, LLM call). Same bus primitive; orthogonal
producers + consumers. A plugin can subscribe to connector
events via the ``on_connector_event`` hook and act on
them — e.g. the audit log plugin logs connector events
alongside tool calls.
"""

from __future__ import annotations

from magi.plugins.base import (
    Hook,
    Plugin,
    PluginContext,
)
from magi.plugins.bus import (
    HookBus,
    emit,
    get_bus,
    register_plugin,
    reset_bus,
    shutdown_all,
)

__all__ = [
    # Protocol types
    "Hook",
    "Plugin",
    "PluginContext",
    # Bus + lifecycle
    "HookBus",
    "get_bus",
    "emit",
    "register_plugin",
    "reset_bus",
    "shutdown_all",
]