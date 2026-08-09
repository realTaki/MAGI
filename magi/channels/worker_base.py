"""ChannelWorker 基类 — 构造注入 Bus，提供 start/stop/health/delivery 模板。"""

from __future__ import annotations

import asyncio
import logging
from abc import abstractmethod
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Awaitable, Callable

from magi.startup.worker import RuntimeWorker

if TYPE_CHECKING:
    from magi.bus import Bus
    from magi.bus.guild.deliveryJob import DeliveryJob, DeliveryResult

logger = logging.getLogger("magi.channels.worker")
_backpressure_last_warn: dict[str, float] = {}


class ChannelWorker(RuntimeWorker):
    """RuntimeWorker extension for channel-specific delivery handling."""

    worker_kind = "channel"
    @property
    @abstractmethod
    def channel_name(self) -> str: ...

    def __init__(self, bus: Bus, *, poll_seconds: float = 0.25) -> None:
        super().__init__(bus, poll_seconds=poll_seconds)
        self.worker_name = self.channel_name
        self.worker_id = f"{self.channel_name}-worker"
        self._queue_depth = 0

    @abstractmethod
    async def _run(self) -> None: ...

    async def _claim_delivery_loop(
        self, deliver_fn: Callable[[DeliveryJob], Awaitable[None]], channel_label: str,
    ) -> None:
        from magi.bus.guild.deliveryJob import DeliveryResult
        max_depth = await self._read_max_queue_depth()
        while not self._stopping:
            depth = await self._read_queue_depth(channel_label)
            if depth > max_depth:
                self._log_backpressure_throttle(channel_label, depth)
                await asyncio.sleep(self.poll_seconds * 5)
                continue
            try:
                job = await self.call(self.bus.delivery_job_board.claim)
            except Exception:
                logger.exception("channels[%s]: claim failed", channel_label)
                await asyncio.sleep(self.poll_seconds)
                continue
            if job is None:
                await asyncio.sleep(self.poll_seconds)
                continue
            if getattr(job, "channel", "") != channel_label:
                try: await self.call(self.bus.delivery_job_board.release, key=job.job_id)
                except Exception: pass
                await asyncio.sleep(0.01)
                continue
            self.polled()
            try:
                await deliver_fn(job)
                await self.call(self.bus.delivery_job_board.submit_result,
                    key=job.job_id, result=DeliveryResult(job_id=job.job_id, success=True))
                self.succeeded()
            except Exception as exc:
                self.failed(exc)
                logger.exception("channels[%s]: delivery %s failed", channel_label, job.job_id)
                await self.call(self.bus.delivery_job_board.submit_result,
                    key=job.job_id, result=DeliveryResult(job_id=job.job_id, success=False, error=str(exc)[:1024]))

    async def _read_max_queue_depth(self) -> int:
        raw = await self.call(self.bus.settings_book.get, key="channels.delivery.max_queue_depth")
        if raw and str(raw).isdigit(): return int(raw)
        return 1000

    async def _read_queue_depth(self, channel_label: str) -> int:
        try:
            self._queue_depth = await self.call(
                self.bus.delivery_job_board.pending_count, channel=channel_label,
            )
        except Exception:
            self._queue_depth = 0
        return self._queue_depth

    def queue_depth(self) -> int | None:
        return getattr(self, "_queue_depth", 0)

    def _log_backpressure_throttle(self, channel_label: str, depth: int) -> None:
        global _backpressure_last_warn
        now = datetime.now(timezone.utc).timestamp()
        last = _backpressure_last_warn.get(channel_label, 0)
        if now - last >= 60:
            _backpressure_last_warn[channel_label] = now
            logger.warning("channels[%s]: backpressure depth=%d, throttling", channel_label, depth)
