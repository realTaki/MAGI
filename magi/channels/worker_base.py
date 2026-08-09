"""ChannelWorker 基类 — 构造注入 Bus，提供 start/stop/health/delivery 模板。"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from contextlib import suppress
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Awaitable, Callable

if TYPE_CHECKING:
    from magi.bus import Bus
    from magi.bus.guild.deliveryJob import DeliveryJob, DeliveryResult

logger = logging.getLogger("magi.channels.worker")
_backpressure_last_warn: dict[str, float] = {}


class ChannelWorker(ABC):
    @property
    @abstractmethod
    def channel_name(self) -> str: ...

    def __init__(self, bus: Bus, *, poll_seconds: float = 0.25) -> None:
        self.bus = bus
        self.poll_seconds = poll_seconds
        self.worker_id = f"{self.channel_name}-worker"
        self._task: asyncio.Task[None] | None = None
        self._stopping = False
        self._last_poll_at: datetime | None = None
        self._last_success_at: datetime | None = None
        self._last_error: str | None = None

    async def start(self) -> None:
        if self._task is not None: return
        self._stopping = False
        self._task = asyncio.create_task(self._run(), name=f"magi-channel-{self.channel_name}")

    async def stop(self) -> None:
        self._stopping = True
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError): await self._task
            self._task = None

    @abstractmethod
    async def _run(self) -> None: ...

    def health(self) -> dict:
        return {
            "name": self.channel_name,
            "running": self._task is not None and not self._task.done(),
            "last_poll_at": self._last_poll_at.isoformat() if self._last_poll_at else None,
            "last_success_at": self._last_success_at.isoformat() if self._last_success_at else None,
            "last_error": self._last_error,
            "queue_depth": self._bus_depth(self.channel_name),
        }

    async def _claim_delivery_loop(
        self, deliver_fn: Callable[[DeliveryJob], Awaitable[None]], channel_label: str,
    ) -> None:
        from magi.bus.guild.deliveryJob import DeliveryResult
        max_depth = self._read_max_queue_depth()
        while not self._stopping:
            depth = self._bus_depth(channel_label)
            if depth > max_depth:
                self._log_backpressure_throttle(channel_label, depth)
                await asyncio.sleep(self.poll_seconds * 5)
                continue
            try:
                job = await asyncio.to_thread(self.bus.delivery_job_board.claim)
            except Exception:
                logger.exception("channels[%s]: claim failed", channel_label)
                await asyncio.sleep(self.poll_seconds)
                continue
            if job is None:
                await asyncio.sleep(self.poll_seconds)
                continue
            if getattr(job, "channel", "") != channel_label:
                try: self.bus.delivery_job_board.release(key=job.job_id)
                except Exception: pass
                await asyncio.sleep(0.01)
                continue
            self._last_poll_at = datetime.now(timezone.utc)
            try:
                await deliver_fn(job)
                self._last_success_at = datetime.now(timezone.utc)
                self._last_error = None
                self.bus.delivery_job_board.submit_result(
                    key=job.job_id, result=DeliveryResult(job_id=job.job_id, success=True))
            except Exception as exc:
                self._last_error = str(exc)
                logger.exception("channels[%s]: delivery %s failed", channel_label, job.job_id)
                self.bus.delivery_job_board.submit_result(
                    key=job.job_id, result=DeliveryResult(job_id=job.job_id, success=False, error=str(exc)[:1024]))

    def _read_max_queue_depth(self) -> int:
        raw = self.bus.settings_book.get(key="channels.delivery.max_queue_depth")
        if raw and str(raw).isdigit(): return int(raw)
        return 1000

    def _bus_depth(self, channel_label: str) -> int:
        try: return self.bus.delivery_job_board.pending_count(channel=channel_label)
        except Exception: return 0

    def _log_backpressure_throttle(self, channel_label: str, depth: int) -> None:
        global _backpressure_last_warn
        now = datetime.now(timezone.utc).timestamp()
        last = _backpressure_last_warn.get(channel_label, 0)
        if now - last >= 60:
            _backpressure_last_warn[channel_label] = now
            logger.warning("channels[%s]: backpressure depth=%d, throttling", channel_label, depth)
