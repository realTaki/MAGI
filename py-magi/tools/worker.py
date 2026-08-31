"""Tools worker: seed the catalog, then claim RunToolJob.

Attach publishes missing builtin rows through ``SetToolJob``. The loop
claims ``RunToolJob`` and ``go()``s the handler. Builtin ``run``
implementations are not attached yet.
"""

from __future__ import annotations

import asyncio
import logging

from bus import (
    BaseWorker,
    JobStatus,
    ListToolsJob,
    RunToolJob,
    RunToolResult,
    SetToolJob,
    go,
)
from tools.registry import builtin_catalog

logger = logging.getLogger("tools.worker")


class ToolsWorker(BaseWorker):
    worker_name = "tools"

    async def on_attached(self) -> None:
        await self._boost_builtins()

    async def _poll(self) -> bool:
        job = await self.claim(RunToolJob)
        if job is None:
            return False
        go(self._on_run(job))
        return True

    async def _boost_builtins(self) -> None:
        try:
            listed = await self.ask(ListToolsJob(include_disabled=True))
            existing = {tool.name for tool in listed.tools}
        except Exception:  # noqa: BLE001 -- missing catalog must not block attach
            logger.exception("tools worker: list tools failed")
            existing = set()
        seeded = 0
        for spec in builtin_catalog():
            if spec["name"] in existing:
                continue
            try:
                await self.ask(
                    SetToolJob(
                        name=spec["name"],
                        description=spec["description"],
                        input_schema=spec["input_schema"],
                        enabled=True,
                    )
                )
            except Exception as exc:  # noqa: BLE001 -- one seed failure must not block the rest
                logger.warning("tools worker: failed to seed %r (%s)", spec["name"], exc)
                continue
            seeded += 1
        if seeded:
            logger.info("tools worker: seeded %d builtin tool(s)", seeded)

    async def _on_run(self, job: RunToolJob) -> None:
        try:
            self._fail(job, "tool execution is not attached")
        except asyncio.CancelledError:
            self._fail(job, "tools worker cancelled")
            raise
        except Exception:  # noqa: BLE001 -- no job can kill the worker
            logger.exception("tools worker: unhandled exception on job %s", job.id)
            self._fail(job, "tool execution is not attached")

    def _fail(self, job: RunToolJob, error: str) -> None:
        self.submit(
            RunToolJob,
            RunToolResult(id=job.id, status=JobStatus.FAILED, error=error),
        )
