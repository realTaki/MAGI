"""Committed-reply sender workers owned by :mod:`magi.channels`."""

from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import suppress

from magi.bus import BusStore, DeliveryClaim
from magi.db import require_state_dir

logger = logging.getLogger("magi.channels.delivery")


class DeliveryWorker:
    """Send durable outbox records without blocking the agent actor."""

    def __init__(self, state_dir: str | None = None, *, poll_seconds: float = 0.25) -> None:
        self.state_dir = state_dir or require_state_dir()
        self.store = BusStore(self.state_dir)
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
            claim = self.store.claim_next_delivery(self.worker_id)
            if claim is None:
                await asyncio.sleep(self.poll_seconds)
                continue
            await self._send(claim)

    async def _send(self, claim: DeliveryClaim) -> None:
        try:
            if claim.channel != "tg" or not claim.destination:
                raise ValueError(f"unsupported delivery channel: {claim.channel!r}")
            from magi.channels.telegram.bot import send_text_raw
            from magi.db.settings import state_get

            token = state_get(self.state_dir, "telegram.bot_token")
            if not token:
                raise RuntimeError("Telegram is not configured")
            await send_text_raw(token, int(claim.destination), str(claim.payload.get("text") or ""))
        except Exception:
            logger.exception("delivery %s failed", claim.delivery_id)
            self.store.retry_delivery(claim.delivery_id)
            return
        self.store.complete_delivery(claim.delivery_id)


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
