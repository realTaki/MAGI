"""Plugin HookBus — process-wide hook fan-out.

Mirrors :mod:`magi.connectors.bus` for connector events;
same shape (subscribe / unsubscribe / emit), same
async-fire-and-forget delivery. Kept in a separate module
so plugins don't need to import connector types and vice
versa — the two are orthogonal producers + consumers but
live in different subsystems.

Why a separate bus rather than reusing the connector bus
---------------------------------------------------------------
Connector events and plugin hooks have different shapes
(``ConnectorEvent`` vs ``PluginContext``). Forcing them
through one type would mean every plugin handler has to
branch on the ``hook`` field. Two buses, one shared
``emit`` shape, clean callers.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from magi.plugins.base import Hook, HookBus, Plugin, PluginContext

logger = logging.getLogger("magi.plugins.bus")


class HookBusImpl:
    """Process-wide hook fan-out.

    Handlers are coroutines; ``emit`` schedules each via
    ``asyncio.create_task`` so a single slow handler doesn't
    block delivery to others. Handlers MUST NOT raise — the
    bus catches and logs.

    Same shape as :class:`magi.connectors.bus.EventBus` —
    subscribe / unsubscribe / emit — but without dedup.
    Hooks aren't deduped because they're internal events,
    not idempotency keys.
    """

    def __init__(self) -> None:
        self._subscribers: dict[str, list] = {}

    def subscribe(
        self,
        kind: "Hook",
        handler,
    ) -> None:
        """Register ``handler`` for hook ``kind``."""
        self._subscribers.setdefault(kind.value, []).append(handler)

    def unsubscribe(
        self,
        kind: "Hook",
        handler,
    ) -> None:
        try:
            self._subscribers[kind.value].remove(handler)
        except (KeyError, ValueError):
            pass

    def emit(self, kind: "Hook", context: "PluginContext") -> None:
        """Schedule ``context`` to every subscriber of ``kind``.

        Synchronous wrapper — call sites are everywhere.
        The bus spawns the coroutine internally.
        """
        handlers = self._subscribers.get(kind.value, ())
        if not handlers:
            return
        for handler in handlers:
            asyncio.create_task(self._invoke(handler, context))

    @staticmethod
    async def _invoke(handler, context) -> None:
        try:
            await handler(context)
        except Exception:
            logger.exception(
                "plugin bus: handler %r raised on hook %s",
                handler, context.hook.value,
            )


# The bus is exposed as ``HookBus`` (alias for the impl)
# so external callers can monkeypatch either the impl or
# the name during tests. Same trick the connector bus
# uses.
HookBus = HookBusImpl


# -- Plugin lifecycle ------------------------------------------------------


_PLUGINS: list["Plugin"] = []


def register_plugin(plugin: "Plugin") -> None:
    """Install ``plugin`` into the runtime.

    Idempotent: re-registering the same ``name`` replaces
    the prior instance (used by tests + hot reload).
    """
    for i, existing in enumerate(_PLUGINS):
        if existing.name == plugin.name:
            _PLUGINS[i] = plugin
            return
    _PLUGINS.append(plugin)


def list_plugins() -> list[str]:
    """All registered plugin names."""
    return [p.name for p in _PLUGINS]


def install_all(bus: "HookBus") -> None:
    """Call ``register(bus)`` on every plugin in
    registration order.

    Boot path uses this once after ``load_plugins()``
    populates the registry. Tests use it to wire a single
    plugin manually.
    """
    for plugin in _PLUGINS:
        try:
            plugin.register(bus)
            logger.info(
                "plugin installed: %s v%s", plugin.name, plugin.version,
            )
        except Exception:
            logger.exception(
                "plugin %r register() raised; skipping", plugin.name,
            )


async def shutdown_all() -> None:
    """Call ``shutdown()`` on every plugin. Idempotent."""
    for plugin in list(_PLUGINS):
        try:
            result = plugin.shutdown()
            if asyncio.iscoroutine(result):
                await result
        except Exception:
            logger.exception(
                "plugin %r shutdown() raised", plugin.name,
            )


def reset() -> None:
    """Drop the registry (test helper)."""
    _PLUGINS.clear()


# -- Module-level singleton + sugar API ------------------------------------


_BUS: "HookBus | None" = None


def get_bus() -> "HookBus":
    """Return the process-wide hook bus, creating on first call."""
    global _BUS
    if _BUS is None:
        _BUS = HookBus()
    return _BUS


def reset_bus() -> None:
    global _BUS
    _BUS = None


def emit(kind: "Hook", context: "PluginContext") -> None:
    """Sugar over :meth:`HookBus.emit` on the global bus."""
    get_bus().emit(kind, context)


__all__ = [
    "HookBus",
    "HookBusImpl",
    "register_plugin",
    "list_plugins",
    "install_all",
    "shutdown_all",
    "reset",
    "get_bus",
    "reset_bus",
    "emit",
]