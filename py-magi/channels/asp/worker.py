"""ASP channel worker: bridge ASP sessions to conversation Jobs."""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import Future
from contextlib import suppress
from typing import Any

from bus import (
    MAGI_CONTACT_ID,
    BaseWorker,
    Bus,
    ChangeProviderNotify,
    ChatNotify,
    DeliveryNotify,
    DeliveryNotifyResult,
    GetContactJob,
    JobStatus,
    UpdateContactJob,
    go,
)

from .client import AspClient
from .intranet import should_join_on_invite

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

    async def _on_event(self, event: dict[str, Any]) -> dict[str, Any] | None:
        kind = event.get("type")
        if kind == "agent.nickname.read":
            result = await self.ask(
                GetContactJob(publisher=self.worker_name, contact_id=MAGI_CONTACT_ID)
            )
            return {
                "type": "agent.nickname.current",
                "nickname": (
                    result.contact.nickname
                    if result is not None and result.contact is not None
                    else None
                ),
            }
        if kind == "agent.nickname.update":
            nickname = event.get("nickname")
            updated = False
            if isinstance(nickname, str) and nickname.strip():
                updated = await self.ask(
                    UpdateContactJob(
                        publisher=self.worker_name,
                        contact_id=MAGI_CONTACT_ID,
                        nickname=nickname.strip(),
                    )
                ) is not None
            return {
                "type": "agent.nickname.updated",
                "request_id": event.get("request_id"),
                "ok": updated,
            }
        if kind == "agent.provider.update":
            # The app owns provider settings; ASP forwards a candidate for the
            # provider worker to verify before MAGI accepts it.
            changed = await self.ask(
                ChangeProviderNotify(
                    publisher=self.worker_name,
                    provider=_setting_text(event.get("provider")),
                    api_key=_setting_text(event.get("api_key")),
                    model=_setting_text(event.get("model")),
                )
            ) is not None
            return {
                "type": "agent.provider.updated",
                "request_id": event.get("request_id"),
                "ok": changed,
            }
        session_id = event.get("session_id")
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        if not isinstance(session_id, str):
            return
        try:
            if kind == "session.invited" and should_join_on_invite(
                origin=self._settings.get("base", ""),
                invitee=str(payload.get("invitee") or ""),
                handle=self.handle,
            ):
                # Intranet magi-asp: join on receipt. No public-network
                # approval and no config wizard.
                client = self._client
                if client is None:
                    return
                await client.join(session_id)
                initial = payload.get("initial_message")
                if isinstance(initial, dict):
                    await self.call(self._ingest, session_id, initial)
            elif kind == "session.message" and payload.get("sender") != self.handle:
                await self.call(self._ingest, session_id, payload)
        except Exception as exc:  # noqa: BLE001 -- one ASP event cannot stop the channel
            logger.exception("could not ingest ASP event %s: %s", event.get("event_id"), exc)
            return None
        event_id = event.get("event_id")
        if isinstance(event_id, str):
            return {"type": "session.ack", "session_id": session_id, "event_id": event_id}
        return None

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

def _setting_text(value: object) -> str | None:
    """A provider setting from ASP: text, or None to leave it unchanged."""
    return value if isinstance(value, str) and value != "" else None


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
