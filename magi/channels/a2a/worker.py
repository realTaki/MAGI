"""A2AWorker — 消费 SendA2AJob 从 a2a_job_board。"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from magi.bus.guild.sendA2AJob import SendA2AResult
from magi.channels.worker_base import ChannelWorker

if TYPE_CHECKING:
    from magi.bus.guild.sendA2AJob import SendA2AJob

logger = logging.getLogger("magi.channels.a2a.worker")


class A2AWorker(ChannelWorker):
    channel_name = "a2a"

    async def _run(self) -> None:
        while not self._stopping:
            try:
                job = await self.call(self.bus.a2a_job_board.claim)
            except Exception:
                logger.exception("A2AWorker: claim failed")
                await asyncio.sleep(self.poll_seconds)
                continue
            if job is None:
                await asyncio.sleep(self.poll_seconds)
                continue
            self.polled()
            try:
                await self._deliver_a2a(job)
                result = SendA2AResult(job_id=job.job_id, success=True, status="delivered")
                await self.call(self.bus.a2a_job_board.submit_result, key=job.job_id, result=result)
                self.succeeded()
            except Exception as exc:
                self.failed(exc)
                logger.exception("A2AWorker: delivery %s failed", job.job_id)
                result = SendA2AResult(
                    job_id=job.job_id, success=False, status="failed", error=str(exc)[:1024]
                )
                await self.call(self.bus.a2a_job_board.submit_result, key=job.job_id, result=result)

    async def _deliver_a2a(self, job: SendA2AJob) -> None:
        from magi.channels.a2a.transport import send_a2a_delivery

        target = int(job.target) if job.target else 0
        if not target:
            raise ValueError("A2A delivery missing target")
        await send_a2a_delivery(self.bus, target, job.job_id, job.request or {})
