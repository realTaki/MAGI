"""A2AWorker — Agent-to-Agent 通道 Worker。

消费 ``SendA2AJob`` 从 ``a2a_job_board``（非 delivery_job_board）。
执行对端 HTTP POST，提交 ``SendA2AResult``。

A2A 不是 delivery channel —— 它有专用的 ``a2a_job_board``。
入站 A2A request 由 FastAPI ``POST /a2a/inbox`` 路由处理。
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from magi.channels.worker_base import ChannelWorker

if TYPE_CHECKING:
    from magi.new_bus import NewBus
    from magi.new_bus.guild.sendA2AJob import SendA2AJob, SendA2AResult

logger = logging.getLogger("magi.channels.a2a.worker")


class A2AWorker(ChannelWorker):
    """A2A 通道 Worker：claim SendA2AJob → HTTP POST → submit SendA2AResult。

    不使用 delivery_job_board。A2A 有独立的 a2a_job_board。
    入站由 FastAPI ``POST /a2a/inbox`` 路由处理。
    """

    channel_name = "a2a"

    async def _run(self) -> None:
        """轮询 a2a_job_board，认领 SendA2AJob，执行对端 HTTP POST。"""
        while not self._stopping:
            try:
                job = await asyncio.to_thread(
                    self.bus.a2a_job_board.claim,
                )
            except Exception:
                logger.exception("A2AWorker: claim failed")
                await asyncio.sleep(self.poll_seconds)
                continue

            if job is None:
                await asyncio.sleep(self.poll_seconds)
                continue

            self._last_poll_at = __import__("datetime").datetime.now(
                __import__("datetime").timezone.utc
            )

            try:
                await self._deliver_a2a(job)
                result = __import__(
                    "magi.new_bus.guild.sendA2AJob", fromlist=["SendA2AResult"]
                ).SendA2AResult(
                    invocation_id=job.invocation_id,
                    success=True,
                    status="delivered",
                )
                self.bus.a2a_job_board.submit_result(
                    key=job.invocation_id,
                    result=result,
                )
            except Exception as exc:
                self._last_error = str(exc)
                logger.exception(
                    "A2AWorker: delivery %s failed", job.invocation_id,
                )
                result = __import__(
                    "magi.new_bus.guild.sendA2AJob", fromlist=["SendA2AResult"]
                ).SendA2AResult(
                    invocation_id=job.invocation_id,
                    success=False,
                    status="failed",
                    error=str(exc)[:1024],
                )
                self.bus.a2a_job_board.submit_result(
                    key=job.invocation_id,
                    result=result,
                )

    async def _deliver_a2a(self, job: SendA2AJob) -> None:
        """HTTP POST 到目标 MAGI。"""
        from magi.channels.a2a.transport import send_a2a_delivery

        target = int(job.target) if job.target else 0
        if not target:
            raise ValueError("A2A delivery missing target")
        payload = job.request or {}
        await send_a2a_delivery(target, job.invocation_id, payload)
