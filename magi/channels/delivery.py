"""Committed-reply sender workers owned by :mod:`magi.channels`."""

from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import suppress

from magi.bus import DeliveryClaim, get_bus
from magi.bus.contracts.session import SessionMessage, utcnow_iso
from magi.constants import STATE_DIR

logger = logging.getLogger("magi.channels.delivery")


class DeliveryWorker:
    """Send durable outbox records without blocking the agent actor."""

    def __init__(self, state_dir: str | None = None, *, poll_seconds: float = 0.25) -> None:
        self.state_dir = state_dir or STATE_DIR
        self.bus = get_bus()
        self.worker_id = f"delivery-{uuid.uuid4().hex}"
        self.poll_seconds = poll_seconds
        self._task: asyncio.Task[None] | None = None
        self._stopping = False

    async def start(self) -> None:
        if self._task is None:
            self._stopping = False
            self._task = asyncio.create_task(self._run(), name="magi-delivery-worker")

    async def stop(self) -> None:
        self._stopping = True
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _run(self) -> None:
        while not self._stopping:
            claim = self.bus.delivery.claim_next(self.worker_id)
            if claim is None:
                await asyncio.sleep(self.poll_seconds)
                continue
            await self._send(claim)

    async def _send(self, claim: DeliveryClaim) -> None:
        try:
            if claim.channel == "tg" and claim.destination:
                from magi.channels.telegram.bot import send_text_raw
                token = self.bus.settings.get("telegram.bot_token")
                if not token:
                    raise RuntimeError("Telegram is not configured")
                await send_text_raw(token, int(claim.destination), str(claim.payload.get("text") or ""))
            elif claim.channel == "a2a" and claim.destination:
                from magi.channels.a2a.transport import send_a2a_delivery

                await send_a2a_delivery(int(claim.destination), claim.delivery_id, claim.payload)
            elif claim.channel == "webui":
                session_id = str(claim.payload.get("session_id") or "")
                uid = claim.payload.get("uid")
                if not session_id or not isinstance(uid, int):
                    raise ValueError("webui delivery is missing session identity")
                self.bus.session.append_messages(
                    uid,
                    session_id,
                    [SessionMessage(
                        role="assistant",
                        text=str(claim.payload.get("text") or ""),
                        ts=utcnow_iso(),
                        message_id=self.bus.session.new_id(),
                    )],
                    channel="webui",
                )
            else:
                raise ValueError(f"unsupported delivery channel: {claim.channel!r}")
        except Exception:
            logger.exception("delivery %s failed", claim.delivery_id)
            self.bus.delivery.retry(claim.delivery_id)
            return
        self.bus.delivery.complete(claim.delivery_id)


_worker: DeliveryWorker | None = None


async def start_delivery_worker(state_dir: str | None = None) -> DeliveryWorker:
    global _worker
    if _worker is None:
        _worker = DeliveryWorker(state_dir)
        await _worker.start()
    return _worker


async def stop_delivery_worker() -> None:
    global _worker
    if _worker is not None:
        await _worker.stop()
        _worker = None
