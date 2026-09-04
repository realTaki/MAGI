"""ASP client for magi-asp: HTTP verbs plus WS /connect."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx
import websockets

OnEvent = Callable[[dict[str, Any]], Awaitable[None]]


class AspClient:
    """One agent on one operator. Auth is Bearer token; identity is handle."""

    def __init__(self, *, handle: str, base: str, token: str) -> None:
        self.handle = handle
        self.base = base.rstrip("/")
        self.token = token
        self._http: httpx.AsyncClient | None = None

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    def _ws_url(self) -> str:
        parsed = urlparse(self.base)
        scheme = "wss" if parsed.scheme == "https" else "ws"
        return urlunparse((scheme, parsed.netloc, "/connect", "", "", ""))

    async def listen(self, on_event: OnEvent, *, ready: asyncio.Event | None = None) -> None:
        async with httpx.AsyncClient(
            base_url=self.base, headers=self._headers, timeout=30.0
        ) as http:
            self._http = http
            async with websockets.connect(
                self._ws_url(), additional_headers=self._headers
            ) as ws:
                if ready is not None:
                    ready.set()
                async for raw in ws:
                    await on_event(json.loads(raw))

    async def join(self, session_id: str) -> None:
        await self._post(f"/sessions/{session_id}/join")

    async def send(self, session_id: str, content: str) -> dict[str, Any]:
        return await self._post(
            f"/sessions/{session_id}/messages", {"content": content}
        )

    async def _post(self, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        http = self._http
        if http is None:
            raise RuntimeError("ASP client is not listening")
        response = await http.post(path) if payload is None else await http.post(path, json=payload)
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, dict) else {}
