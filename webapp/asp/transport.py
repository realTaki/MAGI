"""WebSocket connection registry and event fan-out.

Owns:
  - which agent has which live WS connections (multi-connection per agent)
  - the per-(handle, session_id) cursor for replay
  - the disconnect grace window
  - the actual fire-and-forget delivery of events to live sockets
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Awaitable, Callable

from fastapi import WebSocket

from .store import Event, Store


GRACE_SECONDS = 30


class Transport:
    def __init__(
        self,
        store: Store,
        on_grace_expired: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        self.store = store
        self._connections: dict[str, set[WebSocket]] = {}
        self._cursors: dict[tuple[str, str], int] = {}
        self._disconnect_timers: dict[str, asyncio.Task] = {}
        # Called when an agent's grace window expires while still offline. The
        # service layer uses this to fire session.left and update statuses.
        self._on_grace_expired = on_grace_expired
        # Hook called when an agent (re)comes online. The service layer uses
        # this to fire session.reconnected events and replay missed events.
        self._on_back_online: Callable[[str], Awaitable[None]] | None = None
        # Hook called when an agent's last connection drops (within grace).
        self._on_went_offline: Callable[[str], Awaitable[None]] | None = None

    def set_lifecycle_hooks(
        self,
        on_went_offline: Callable[[str], Awaitable[None]],
        on_back_online: Callable[[str], Awaitable[None]],
    ) -> None:
        self._on_went_offline = on_went_offline
        self._on_back_online = on_back_online

    # ---- Connection lifecycle -------------------------------------------

    async def connect(self, handle: str, ws: WebSocket) -> None:
        was_empty = handle not in self._connections or not self._connections[handle]
        self._connections.setdefault(handle, set()).add(ws)

        # Cancel any pending grace timer.
        timer = self._disconnect_timers.pop(handle, None)
        if timer is not None:
            timer.cancel()

        if was_empty and self._on_back_online is not None:
            await self._on_back_online(handle)

    async def disconnect(self, handle: str, ws: WebSocket) -> None:
        conns = self._connections.get(handle)
        if not conns:
            return
        conns.discard(ws)
        if conns:
            return
        # Last connection dropped — start grace window.
        if self._on_went_offline is not None:
            await self._on_went_offline(handle)
        loop = asyncio.get_event_loop()
        self._disconnect_timers[handle] = loop.create_task(self._grace_timer(handle))

    async def _grace_timer(self, handle: str) -> None:
        try:
            await asyncio.sleep(GRACE_SECONDS)
        except asyncio.CancelledError:
            return
        # Still no connections? Promote to permanent leave.
        if not self._connections.get(handle):
            self._disconnect_timers.pop(handle, None)
            if self._on_grace_expired is not None:
                await self._on_grace_expired(handle)

    def is_online(self, handle: str) -> bool:
        return bool(self._connections.get(handle))

    # ---- Delivery --------------------------------------------------------

    async def deliver(self, handle: str, event: Event) -> None:
        """Send an event to all live connections for `handle`. Updates cursor
        for session events. No-op if the agent is offline."""
        conns = self._connections.get(handle)
        if not conns:
            return
        wire = json.dumps(event.to_wire())
        dead: list[WebSocket] = []
        for ws in list(conns):
            try:
                await ws.send_text(wire)
            except Exception:
                dead.append(ws)
        for ws in dead:
            conns.discard(ws)
        if event.session_id is not None and event.sequence is not None:
            self._cursors[(handle, event.session_id)] = event.sequence

    def cursor(self, handle: str, session_id: str) -> int:
        """Last delivered sequence for this agent in this session, or -1 if none."""
        return self._cursors.get((handle, session_id), -1)

    def advance_cursor(self, handle: str, session_id: str, sequence: int) -> None:
        current = self._cursors.get((handle, session_id), -1)
        if sequence > current:
            self._cursors[(handle, session_id)] = sequence
