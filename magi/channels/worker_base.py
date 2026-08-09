"""ChannelWorker 基类 — 所有 Channel Worker 的抽象父类。

遵循 new_bus 构造器注入模式，与 :class:`magi.tools.worker.ToolsWorker`、
:class:`magi.providers.worker.ProvidersWorker` 对齐。

出站 claim loop 由 :meth:`_claim_delivery_loop` 模板方法提供，子类只需实现
``_deliver_X(job: DeliveryJob) -> None``。

Delivery retry 由 ``BaseJobBoard._claim``（``magi/new_bus/guild/base.py``）
负责：abandoned 的 DeliveryJob 在 lease 过期后被重 claim，
最多 ``MAX_ATTEMPTS=3`` 后 ``_make_exhausted_result`` 标记 failed。
Channel Worker 不自己重试。

.. code-block:: text

    Phase 1 Verification（Part A-F 全部落地后跑）:
    - [ ] ``grep -rE "from magi.bus import get_bus" magi/channels/`` → 0 hits
    - [ ] ``_runtime_lifespan`` 起/停 4 个 Channel Worker
    - [ ] TG inbound ChatJob 到达 AgentWorker
    - [ ] TG outbound DeliveryJob 到达 TG API
    - [ ] TaskWorker cron 任务每个 tick 最多触发一次
    - [ ] TaskWorker ``run_at`` 任务恰好触发一次
    - [ ] ``RunTaskJob`` 从 ``schedule_task`` tool 流转到 TaskRun
    - [ ] ``/health/channels`` 返回 4 通道 JSON
    - [ ] pending depth > 1000 触发节流警告
    - [ ] ``pytest`` 全部 pass；TODO 注释全部移除
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from contextlib import suppress
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Awaitable, Callable

if TYPE_CHECKING:
    from magi.new_bus import NewBus
    from magi.new_bus.guild.deliveryJob import DeliveryJob, DeliveryResult

logger = logging.getLogger("magi.channels.worker")

# backpressure per-channel warn throttle — one warning per (channel) per minute
_backpressure_last_warn: dict[str, float] = {}


class ChannelWorker(ABC):
    """所有 Channel Worker 的基类。

    子类只需实现 :meth:`_run`，定义自己的入站/出站轮询逻辑。
    出站 Worker 使用 :meth:`_claim_delivery_loop` 模板。
    """

    @property
    @abstractmethod
    def channel_name(self) -> str:
        """通道标识，如 ``"tg"``、``"task"``、``"webui"``、``"a2a"``。"""
        ...

    def __init__(
        self,
        bus: NewBus,
        *,
        poll_seconds: float = 0.25,
    ) -> None:
        self.bus = bus
        self.poll_seconds = poll_seconds
        self.worker_id = f"{self.channel_name}-worker"
        self._task: asyncio.Task[None] | None = None
        self._stopping = False
        # observability
        self._last_poll_at: datetime | None = None
        self._last_success_at: datetime | None = None
        self._last_error: str | None = None

    async def start(self) -> None:
        """启动 worker 的轮询循环。幂等。"""
        if self._task is not None:
            return
        self._stopping = False
        self._task = asyncio.create_task(
            self._run(), name=f"magi-channel-{self.channel_name}"
        )
        logger.info("channel worker %s started", self.channel_name)

    async def stop(self) -> None:
        """停止 worker，取消轮询任务。幂等。"""
        self._stopping = True
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        logger.info("channel worker %s stopped", self.channel_name)

    @abstractmethod
    async def _run(self) -> None:
        """子类实现：定义自己的轮询循环。"""
        ...

    # -- observability -----------------------------------------------------

    def health(self) -> dict:
        """返回 worker 健康状态快照。"""
        return {
            "name": self.channel_name,
            "running": self._task is not None and not self._task.done(),
            "last_poll_at": self._last_poll_at.isoformat() if self._last_poll_at else None,
            "last_success_at": self._last_success_at.isoformat() if self._last_success_at else None,
            "last_error": self._last_error,
            "queue_depth": self._bus_depth(self.channel_name),
        }

    # -- delivery claim loop template -------------------------------------

    async def _claim_delivery_loop(
        self,
        deliver_fn: Callable[[DeliveryJob], Awaitable[None]],
        channel_label: str,
    ) -> None:
        """模板方法：backpressure check → claim → deliver_fn → submit_result。

        ``deliver_fn`` 是 async (DeliveryJob) -> None，失败时 raise。
        本方法处理 backpressure throttle + claim 异常 +
        submit_result 一次（成功或失败）。

        Delivery retry 由 BaseJobBoard._claim lease 机制处理；本方法只做一次提交。
        """
        from magi.new_bus.guild.deliveryJob import DeliveryResult

        max_depth = self._read_max_queue_depth()
        while not self._stopping:
            # ── backpressure ──────────────────────────────────────────
            depth = self._bus_depth(channel_label)
            if depth > max_depth:
                self._log_backpressure_throttle(channel_label, depth)
                await asyncio.sleep(self.poll_seconds * 5)
                continue

            # ── claim ─────────────────────────────────────────────────
            try:
                job = await asyncio.to_thread(
                    self.bus.delivery_job_board.claim,
                )
            except Exception:
                logger.exception("channels[%s]: claim failed", channel_label)
                await asyncio.sleep(self.poll_seconds)
                continue
            if job is None:
                await asyncio.sleep(self.poll_seconds)
                continue

            # 跳过不属于本 channel 的 job（claim 无 channel filter，
            # 需要手动检查并 release 给其他 worker）
            if getattr(job, "channel", "") != channel_label:
                try:
                    self.bus.delivery_job_board.release(key=job.job_id)
                except Exception:
                    pass
                await asyncio.sleep(0.01)
                continue

            self._last_poll_at = datetime.now(timezone.utc)

            # ── deliver + submit_result ───────────────────────────────
            try:
                await deliver_fn(job)
                self._last_success_at = datetime.now(timezone.utc)
                self._last_error = None
                self.bus.delivery_job_board.submit_result(
                    key=job.job_id,
                    result=DeliveryResult(job_id=job.job_id, success=True),
                )
            except Exception as exc:
                self._last_error = str(exc)
                logger.exception(
                    "channels[%s]: delivery %s failed",
                    channel_label, job.job_id,
                )
                self.bus.delivery_job_board.submit_result(
                    key=job.job_id,
                    result=DeliveryResult(
                        job_id=job.job_id, success=False,
                        error=str(exc)[:1024],
                    ),
                )

    def _read_max_queue_depth(self) -> int:
        """读背压队列深度阈值。默认 1000。"""
        raw = self.bus.settings_book.get(key="channels.delivery.max_queue_depth")
        if raw and str(raw).isdigit():
            return int(raw)
        return 1000

    def _bus_depth(self, channel_label: str) -> int:
        """读取特定 channel 的 pending job 数量。"""
        try:
            return self.bus.delivery_job_board.pending_count(
                channel=channel_label,
            )
        except Exception:
            return 0

    def _log_backpressure_throttle(self, channel_label: str, depth: int) -> None:
        """背压日志：每个 channel 每分钟最多一条 warning。"""
        global _backpressure_last_warn
        now = datetime.now(timezone.utc).timestamp()
        last = _backpressure_last_warn.get(channel_label, 0)
        if now - last >= 60:
            _backpressure_last_warn[channel_label] = now
            logger.warning(
                "channels[%s]: backpressure — queue depth %d > max. throttling.",
                channel_label, depth,
            )
