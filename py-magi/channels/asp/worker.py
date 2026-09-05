"""ASP channel worker: bridge ASP sessions to conversation Jobs."""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import Future
from contextlib import suppress
from typing import Any

from bus import (
    BaseWorker,
    Bus,
    ChatNotify,
    DeliveryNotify,
    DeliveryNotifyResult,
    JobStatus,
    go,
)

from .client import AspClient

logger = logging.getLogger("channels.asp.worker")


class AspWorker(BaseWorker):
    """Receive ASP messages and deliver conversation replies through ASP."""

    worker_name = "asp"

    def __init__(self, bus: Bus, *, poll_seconds: float = 0.25) -> None:
        super().__init__(bus, poll_seconds=poll_seconds)
        self.handle = bus.handle
        self._client: AspClient | None = None
        self._listen: Future[None] | None = None

    async def on_attached(self) -> None:
        self.handle = self._settings.get("handle") or self.bus.handle
        self._client = AspClient(
            handle=self.handle,
            base=self._settings.get("base", ""),
            token=self._settings.get("token", ""),
        )
        ready = asyncio.Event()
        listen = go(self._client.listen(self._on_event, ready=ready))
        self._listen = listen
        connected = go(ready.wait())
        await asyncio.wait(
            {asyncio.wrap_future(listen), asyncio.wrap_future(connected)},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if not connected.done():
            connected.cancel()
        if listen.done():
            listen.result()
        listen.add_done_callback(_report_listener_exit)

    async def on_detached(self) -> None:
        listen = self._listen
        self._listen = None
        if listen is not None and not listen.done():
            listen.cancel()
        if listen is not None:
            with suppress(asyncio.CancelledError):
                await asyncio.wrap_future(listen)

    async def _poll(self) -> bool:
        board = self.board(DeliveryNotify)
        if board is None:
            return False
        job = await self.call(board.claim_for_channel, "asp")
        if job is None:
            return False
        go(self._deliver(job))
        return True

    async def _on_event(self, event: dict[str, Any]) -> None:
        kind = event.get("type")
        session_id = event.get("session_id")
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        if not isinstance(session_id, str):
            return
        try:
            if kind == "session.invited" and payload.get("invitee") == self.handle:
                await self._client.join(session_id)
                initial = payload.get("initial_message")
                if isinstance(initial, dict):
                    await self.call(self._ingest, session_id, initial)
            elif kind == "session.message" and payload.get("sender") != self.handle:
                await self.call(self._ingest, session_id, payload)
        except Exception as exc:  # noqa: BLE001 -- one ASP event cannot stop the channel
            await self._tell(session_id, f"[asp error]\n{exc}")

    def _ingest(self, session_id: str, payload: dict[str, Any]) -> None:
        text = _content_text(payload.get("content"))
        if not text:
            return
        self.publish(
            ChatNotify(
                publisher=self.handle,
                channel="asp",
                delivery_address=session_id,
                text=text,
            )
        )

    async def _deliver(self, job: DeliveryNotify) -> None:
        if job.channel != "asp" or not job.address or not job.text:
            await self._submit_delivery(
                DeliveryNotifyResult(
                    id=job.id,
                    status=JobStatus.FAILED,
                    error="no ASP session for this conversation",
                )
            )
            return
        try:
            await self._client.send(job.address, job.text)
        except Exception as exc:  # noqa: BLE001 -- delivery failure belongs to its Job
            await self._submit_delivery(
                DeliveryNotifyResult(id=job.id, status=JobStatus.FAILED, error=str(exc))
            )
            return
        await self._submit_delivery(DeliveryNotifyResult(id=job.id))

    async def _submit_delivery(self, result: DeliveryNotifyResult) -> None:
        self.submit(DeliveryNotify, result)

    async def _tell(self, session_id: str, text: str) -> None:
        if self._client is None:
            return
        with suppress(Exception):
            await self._client.send(session_id, text)


def _content_text(content: object) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "".join(parts).strip()
    return ""


def _report_listener_exit(future: Future[None]) -> None:
    if future.cancelled():
        return
    exc = future.exception()
    if exc is not None:
        logger.error("ASP listener stopped", exc_info=exc)
