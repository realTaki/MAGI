"""Connector event bus.

In-process pub/sub. Subscribers are coroutines; the bus
fires them via ``asyncio.create_task`` so a slow subscriber
doesn't block upstream delivery. Handlers MUST NOT raise
— the bus catches + logs and moves on. Retry semantics
are the subscriber's problem.

Why in-process only
-------------------
Cross-process event sharing would route through a2a
(deferred). Keeping the bus in-process keeps the connector
subsystem simple and matches the rest of MAGI's
"single-process runtime" model.

The bus is shared with the plugin subsystem —
:mod:`magi.plugins.bus` re-exports the primitives so a
single subscriber can listen to both connector events and
plugin lifecycle events through one API.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from magi.connectors.base import ConnectorEvent, EventHandler

logger = logging.getLogger("magi.connectors.bus")


class EventBus:
    """Async fan-out for connector events.

    Subscribers are stored as plain callables (coroutines).
    ``publish`` schedules each one via ``asyncio.create_task``
    so a single slow handler can't block delivery to others.
    The bus also de-duplicates by ``event.key()`` within a
    short window so a redelivery from a reconnecting connector
    doesn't double-fire.
    """

    def __init__(self, *, dedup_window_seconds: float = 5.0) -> None:
        self._subscribers: dict[str, list["EventHandler"]] = defaultdict(list)
        # ``kind -> set of seen keys`` — pruned lazily on each
        # ``publish`` so the set never grows unbounded. The
        # dedup window is short on purpose: real upstream
        # redeliveries (Gmail push, webhook retry) happen
        # within seconds, not minutes.
        self._seen: dict[str, dict[tuple[str, str], float]] = defaultdict(dict)
        self._dedup_window = dedup_window_seconds
        self._lock = asyncio.Lock()

    def subscribe(
        self,
        kind: str,
        handler: "EventHandler",
    ) -> None:
        """Register ``handler`` to be called for every
        event with ``event.kind == kind``.

        Multiple subscribers per kind are allowed; each
        gets the event independently. Handler ordering is
        registration order.
        """
        self._subscribers[kind].append(handler)

    def unsubscribe(
        self,
        kind: str,
        handler: "EventHandler",
    ) -> None:
        try:
            self._subscribers[kind].remove(handler)
        except ValueError:
            pass

    async def publish(self, event: "ConnectorEvent") -> None:
        """Fan out an event to every subscriber for its kind.

        Dedup: if the same ``(connector, id)`` was published
        for the same ``kind`` within ``dedup_window_seconds``
        the event is dropped. This is the safeguard against
        webhook redeliveries + reconnect storms.
        """
        # Lazy prune of the dedup window.
        async with self._lock:
            now = asyncio.get_event_loop().time()
            cutoff = now - self._dedup_window
            seen_for_kind = self._seen[event.kind.value]
            stale = [k for k, ts in seen_for_kind.items() if ts < cutoff]
            for k in stale:
                seen_for_kind.pop(k, None)

            key = event.key() + (event.kind.value,)
            if key in seen_for_kind:
                logger.debug(
                    "bus.publish: dedup drop %s key=%s",
                    event.kind.value, event.key(),
                )
                return
            seen_for_kind[key] = now

            handlers = list(self._subscribers.get(event.kind.value, ()))
            subscribers_total = len(handlers)

        logger.debug(
            "bus.publish: kind=%s id=%s subscribers=%d",
            event.kind.value, event.id, subscribers_total,
        )
        # Schedule outside the lock so a slow handler
        # doesn't delay the next publish.
        for handler in handlers:
            asyncio.create_task(self._invoke(handler, event))

    @staticmethod
    async def _invoke(
        handler: "EventHandler",
        event: "ConnectorEvent",
    ) -> None:
        """Call one handler with one event, swallowing errors.

        Handlers that need retry must do it themselves
        (e.g. by republishing from inside the handler on
        a delay). The bus never retries.
        """
        try:
            await handler(event)
        except Exception:
            logger.exception(
                "connector bus: handler %r raised on %s/%s",
                handler, event.connector, event.id,
            )


# -- Module-level singleton + sugar API --------------------------------------

_BUS: EventBus | None = None


def get_bus() -> EventBus:
    """Return the process-wide event bus, creating on first call."""
    global _BUS
    if _BUS is None:
        _BUS = EventBus()
    return _BUS


def reset_bus() -> None:
    """Drop the bus (test helper).

    The next :func:`get_bus` call rebuilds it. Subscribers
    are NOT called on the dropped bus.
    """
    global _BUS
    _BUS = None


def subscribe(
    kind: str,
    handler: "EventHandler",
) -> None:
    """Sugar over :meth:`EventBus.subscribe` on the global bus."""
    get_bus().subscribe(kind, handler)


def unsubscribe(
    kind: str,
    handler: "EventHandler",
) -> None:
    get_bus().unsubscribe(kind, handler)


def publish(event: "ConnectorEvent") -> None:
    """Schedule ``event`` on the global bus.

    Synchronous wrapper because call sites are everywhere —
    the bus does the asyncio.create_task internally.
    """
    asyncio.create_task(get_bus().publish(event))


__all__ = [
    "EventBus",
    "get_bus",
    "reset_bus",
    "subscribe",
    "unsubscribe",
    "publish",
]