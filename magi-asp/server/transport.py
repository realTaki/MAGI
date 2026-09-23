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
from uuid import uuid4

from fastapi import WebSocket

from .store import Event, Store

GRACE_SECONDS = 30
# A control message (nickname, provider settings) waits this long for the MAGI
# to confirm it applied the change.
CONTROL_TIMEOUT_SECONDS = 5


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
        self._nickname_requests: dict[str, tuple[str, asyncio.Future[bool]]] = {}
        self._provider_requests: dict[str, tuple[str, asyncio.Future[bool]]] = {}
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
        await ws.send_text(json.dumps({"type": "agent.nickname.read"}))

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
        for key in [key for key in self._cursors if key[0] == handle]:
            del self._cursors[key]
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

    async def update_nickname(self, handle: str, nickname: str) -> bool:
        return await self._control_request(
            handle,
            {"type": "agent.nickname.update", "nickname": nickname},
            self._nickname_requests,
        )

    async def update_provider(
        self,
        handle: str,
        *,
        provider: str | None,
        model: str | None,
        api_key: str | None,
    ) -> bool:
        """Hand one MAGI the operator's provider settings and await its ack."""
        return await self._control_request(
            handle,
            {
                "type": "agent.provider.update",
                "provider": provider,
                "model": model,
                "api_key": api_key,
            },
            self._provider_requests,
        )

    async def _control_request(
        self,
        handle: str,
        payload: dict[str, Any],
        pending_requests: dict[str, tuple[str, asyncio.Future[bool]]],
    ) -> bool:
        connections = self._connections.get(handle)
        if not connections:
            raise ConnectionError("MAGI is offline")
        request_id = uuid4().hex
        response: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        pending_requests[request_id] = (handle, response)
        try:
            await next(iter(connections)).send_text(
                json.dumps({**payload, "request_id": request_id})
            )
            return await asyncio.wait_for(response, timeout=CONTROL_TIMEOUT_SECONDS)
        finally:
            pending_requests.pop(request_id, None)

    def receive_control(self, handle: str, message: dict[str, Any]) -> None:
        kind = message.get("type")
        if kind == "agent.nickname.current":
            agent = self.store.get_agent(handle)
            nickname = message.get("nickname")
            if agent is not None and (nickname is None or isinstance(nickname, str)):
                agent.nickname = nickname
                self.store.update_agent(agent)
            return
        if kind == "agent.nickname.updated":
            pending_requests = self._nickname_requests
        elif kind == "agent.provider.updated":
            pending_requests = self._provider_requests
        else:
            return
        request_id = message.get("request_id")
        if not isinstance(request_id, str):
            return
        pending = pending_requests.get(request_id)
        if pending is not None and pending[0] == handle and not pending[1].done():
            pending[1].set_result(message.get("ok") is True)

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
        return max(self._cursors.get((handle, session_id), -1),
                   self.store.ack_cursor(handle, session_id))

    def advance_cursor(self, handle: str, session_id: str, sequence: int) -> None:
        current = self._cursors.get((handle, session_id), -1)
        if sequence > current:
            self._cursors[(handle, session_id)] = sequence

    async def close(self) -> None:
        """Cancel grace tasks and close every connection during service shutdown."""
        for timer in self._disconnect_timers.values():
            timer.cancel()
        self._disconnect_timers.clear()
        sockets = [socket for peers in self._connections.values() for socket in peers]
        self._connections.clear()
        for socket in sockets:
            try:
                await socket.close()
            except Exception:
                pass
