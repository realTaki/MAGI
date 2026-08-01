"""Plugin protocol + hook enum + PluginContext.

A :class:`Plugin` is the smallest possible thing the
runtime knows how to load. ``register(bus)`` is the only
required method — the plugin subscribes to whichever hooks
it cares about and the bus schedules each subscriber's
callback as a background asyncio task.

Why a separate type from ``magi.connectors.Connector``:
connectors carry state (open sockets, fetch results); plugins
are passive subscribers. Same bus primitive; orthogonal
producers (connectors) vs observers (plugins).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol, runtime_checkable


class Hook(str, enum.Enum):
    """All hook points a plugin can subscribe to.

    Adding a new hook:

      1. Add the value here.
      2. Call ``emit(Hook.NEW, context)`` at the
         appropriate call site in the runtime.
      3. Document the context payload in :class:`PluginContext`.

    Hook ordering is *registration order* — the audit_log
    plugin that registers first sees events before the cost
    counter. Plugins that need a specific ordering should
    register in priority order at boot.
    """

    BEFORE_TOOL_CALL = "before_tool_call"
    AFTER_TOOL_CALL = "after_tool_call"
    BEFORE_LLM_CALL = "before_llm_call"
    AFTER_LLM_CALL = "after_llm_call"
    BEFORE_CHANNEL_SEND = "before_channel_send"
    AFTER_CHANNEL_SEND = "after_channel_send"
    BEFORE_CONNECTOR_FETCH = "before_connector_fetch"
    AFTER_CONNECTOR_FETCH = "after_connector_fetch"
    ON_CONNECTOR_EVENT = "on_connector_event"
    ON_SESSION_OPEN = "on_session_open"
    ON_SESSION_CLOSE = "on_session_close"


# -- Hook context ----------------------------------------------------------


@dataclass
class PluginContext:
    """Single argument passed to every plugin hook handler.

    Fields are populated by the runtime at the emit site.
    Plugins MUST treat unknown fields as optional — the
    runtime grows new fields over time and old plugins
    ignore what they don't understand.
    """

    hook: Hook
    """Which hook is firing."""

    occurred_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc),
    )
    """When the runtime emitted this hook."""

    # The remaining fields are populated selectively
    # depending on the hook kind. All are optional.

    # Tool hooks — populated when ``hook in (BEFORE_/AFTER_)TOOL_CALL``.
    tool_name: str | None = None
    tool_input: dict[str, Any] | None = None
    tool_result: Any = None
    tool_is_error: bool = False

    # LLM hooks — populated when ``hook in (BEFORE_/AFTER_)LLM_CALL``.
    llm_provider: str | None = None
    llm_model: str | None = None
    llm_input_tokens: int | None = None
    llm_output_tokens: int | None = None
    llm_error: str | None = None

    # Channel hooks — populated when ``hook in (BEFORE_/AFTER_)CHANNEL_SEND``.
    channel: str | None = None
    channel_target_uid: int | None = None
    channel_text: str | None = None
    channel_error: str | None = None

    # Connector hooks — populated when ``hook in (BEFORE_/AFTER_)CONNECTOR_FETCH``
    # or ``ON_CONNECTOR_EVENT``.
    connector: str | None = None
    connector_instance: str | None = None
    connector_query: dict[str, Any] | None = None
    connector_result: Any = None
    connector_event: Any = None  # ``magi.connectors.base.ConnectorEvent``

    # Session hooks — populated when ``hook in (ON_SESSION_OPEN/CLOSE)``.
    session_id: str | None = None
    session_uid: int | None = None
    session_channel: str | None = None


# -- Plugin protocol -------------------------------------------------------


@runtime_checkable
class Plugin(Protocol):
    """The contract every plugin implements.

    Plugins are *passive subscribers*. They MUST NOT raise;
    the bus catches + logs and continues. They MUST NOT
    block — heavy work goes through ``asyncio.create_task``.
    """

    name: str
    """Stable id, surfaces in logs and the future WebUI
    plugins panel."""

    version: str
    """Semver-ish string (``"0.1.0"``). Surfaces in logs
    so operators can see what's loaded."""

    def register(self, bus: "HookBus") -> None:
        """Subscribe to one or more hooks.

        The plugin calls ``bus.subscribe(Hook.AFTER_TOOL_CALL, my_handler)``
        for each hook it cares about. Multiple subscriptions
        per plugin are fine.
        """
        ...

    def shutdown(self) -> None:
        """Flush buffers, close files, cancel background
        tasks. Called at runtime shutdown.

        Optional — plugins with no cleanup needs may use
        a no-op default.
        """
        ...


__all__ = ["Hook", "PluginContext", "Plugin"]