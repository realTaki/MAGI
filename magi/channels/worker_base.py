"""ChannelWorker 基类 — 构造注入 Bus，提供 start/stop/health/delivery 模板。"""

from __future__ import annotations

import asyncio
import logging
import uuid
from abc import abstractmethod
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, ClassVar

from magi.old_bus.bases.job import JobStatus
from magi.runtime_worker import RuntimeWorker

if TYPE_CHECKING:
    from magi.old_bus import Bus
    from magi.old_bus.firmwares.jobs.deliveryNotifyJob import DeliveryNotifyJob

logger = logging.getLogger("magi.channels.worker")
_backpressure_last_warn: dict[str, float] = {}


class ChannelWorker(RuntimeWorker):
    """RuntimeWorker extension for channel-specific delivery handling."""

    worker_kind = "channel"
    # Subclasses MUST override with a string literal (e.g.
    # ``channel_name = "tg"``). Declared as ``ClassVar[str]`` so
    # Pylance accepts the literal override without forcing every
    # subclass to write a property stub. The init-time use of
    # ``self.channel_name`` below catches a forgotten override
    # immediately (worker_name/worker_id come out as empty
    # strings, not a delayed NotImplementedError).
    channel_name: ClassVar[str]

    def __init__(
        self,
        bus: Bus,
        *,
        poll_seconds: float = 0.25,
        concurrency: int | None = None,
    ) -> None:
        super().__init__(bus, poll_seconds=poll_seconds, concurrency=concurrency)
        self.worker_name = self.channel_name
        # 父类 :meth:`RuntimeWorker.__init__` 是在 ``worker_name`` 还是默认
        # ``"worker"`` 时生成的 ``self.worker_id``; 这里用 ``channel_name``
        # 重生成一次, 让 ``worker_id`` 前缀跟 channel 对齐 —— 每次重启都
        # 换, 避免「同 channel 起两个 adapter 时各自的 lease 互踩」。
        self.worker_id = f"{self.channel_name}-{uuid.uuid4().hex}"
        self._queue_depth = 0

    async def register_channel(self) -> None:
        """Advertise this worker's channel in the BUS settings registry."""
        await self.call(self.bus.settings_book.register_channel, name=self.channel_name)

    async def on_start(self) -> bool | None:
        await self.register_channel()
        return None

    @abstractmethod
    async def _run(self) -> None: ...

    async def _claim_delivery_loop(
        self,
        deliver_fn: Callable[[DeliveryNotifyJob], Awaitable[None]],
        channel_label: str,
    ) -> None:
        """Channel-scoped delivery claim loop.

        Uses :meth:`deliveryNotifyJobBoard.claim_for_channel` so each worker
        only reads its own row slice. The previous "claim any, release
        mismatches" pattern (P1 issue in the 2026-08-10 architecture
        review) caused every worker to thrash on rows it didn't own
        and amplified duplicate-delivery risk under restart.
        """
        max_depth = await self._read_max_queue_depth()
        while not self._stopping:
            depth = await self._read_queue_depth(channel_label)
            if depth > max_depth:
                self._log_backpressure_throttle(channel_label, depth)
                await asyncio.sleep(self.poll_seconds * 5)
                continue
            await self.reserve_capacity()
            try:
                job = await self.call(
                    self.bus.delivery_notify_job_board.claim_for_channel,
                    channel=channel_label,
                    worker_id=self.worker_id,
                )
            except Exception:
                self.release_capacity()
                logger.exception("channels[%s]: claim failed", channel_label)
                await asyncio.sleep(self.poll_seconds)
                continue
            if job is None:
                self.release_capacity()
                await asyncio.sleep(self.poll_seconds)
                continue
            # ``claim_for_channel`` filters at the SQL layer. A mismatched
            # row therefore signals storage corruption or a board bug; do not
            # deliver it and let its lease expire for later diagnosis.
            if getattr(job, "channel", "") != channel_label:
                logger.warning(
                    "channels[%s]: claim_for_channel returned a row with channel=%r; leaving lease untouched",
                    channel_label,
                    getattr(job, "channel", None),
                )
                self.release_capacity()
                continue
            self.spawn_reserved(
                self._deliver_claimed(job, deliver_fn, channel_label),
                name=f"{channel_label}-delivery-{job.job_id}",
            )

    async def _deliver_claimed(
        self,
        job: DeliveryNotifyJob,
        deliver_fn: Callable[[DeliveryNotifyJob], Awaitable[None]],
        channel_label: str,
    ) -> None:
        """Deliver one already-claimed row under RuntimeWorker capacity."""
        from magi.old_bus.firmwares.jobs.deliveryNotifyJob import DeliveryNotifyResult

        self.polled()
        try:
            await deliver_fn(job)
            await self.call(
                self.bus.delivery_notify_job_board.submit_result,
                job_id=job.job_id,
                worker_id=self.worker_id,
                result=DeliveryNotifyResult(job_id=job.job_id, status=JobStatus.COMPLETED),
            )
            self.succeeded()
        except Exception as exc:
            self.failed(exc)
            logger.exception("channels[%s]: delivery %s failed", channel_label, job.job_id)
            await self.call(
                self.bus.delivery_notify_job_board.submit_result,
                job_id=job.job_id,
                worker_id=self.worker_id,
                result=DeliveryNotifyResult(job_id=job.job_id, status=JobStatus.FAILED, error=str(exc)[:1024]),
            )

    async def _read_max_queue_depth(self) -> int:
        raw = await self.call(self.bus.settings_book.get_value, key="channels.delivery.max_queue_depth")
        if raw and str(raw).isdigit():
            return int(raw)
        return 1000

    async def _read_queue_depth(self, channel_label: str) -> int:
        try:
            self._queue_depth = await self.call(
                self.bus.delivery_notify_job_board.pending_count,
                channel=channel_label,
            )
        except Exception:
            self._queue_depth = 0
        return self._queue_depth

    def queue_depth(self) -> int | None:
        return getattr(self, "_queue_depth", 0)

    def _log_backpressure_throttle(self, channel_label: str, depth: int) -> None:
        global _backpressure_last_warn
        now = datetime.now(UTC).timestamp()
        last = _backpressure_last_warn.get(channel_label, 0)
        if now - last >= 60:
            _backpressure_last_warn[channel_label] = now
            logger.warning("channels[%s]: backpressure depth=%d, throttling", channel_label, depth)
