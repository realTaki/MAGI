"""Tools worker: seed the catalog, then claim RunToolJob.

Attach publishes missing builtin rows through ``SetToolJob``. The loop
claims ``RunToolJob`` and ``go()``s the handler. Builtin ``run``
implementations are not attached yet.
"""

from __future__ import annotations

import asyncio
import logging
import time

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

_RESULT_TIMEOUT = 5.0


class ToolsWorker(BaseWorker):
    worker_name = "tools"

    async def on_attached(self) -> None:
        await self.call(self._boost_builtins)

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                job = await self.call(self._board(RunToolJob).claim)
                if job is not None:
                    go(self._on_run(job))
                    continue
            except Exception:  # noqa: BLE001 -- a BUS blip must not kill the loop
                logger.exception("tools worker: BUS operation failed")
            await asyncio.sleep(self.poll_seconds)

    def _board(self, job_type):
        assert self.bus is not None
        board = self.bus.board(job_type)
        return board

    def _boost_builtins(self) -> None:
        try:
            existing = self._listed_names()
        except Exception:  # noqa: BLE001 -- missing catalog must not block attach
            logger.exception("tools worker: list tools failed")
            existing = set()
        board = self._board(SetToolJob)
        seeded = 0
        for spec in builtin_catalog():
            if spec["name"] in existing:
                continue
            try:
                job_id = board.publish(
                    SetToolJob(
                        name=spec["name"],
                        description=spec["description"],
                        input_schema=spec["input_schema"],
                        enabled=True,
                    )
                )
                result = _job_result(board, job_id)
            except Exception:  # noqa: BLE001 -- one seed failure must not block the rest
                logger.exception("tools worker: failed to seed %r", spec["name"])
                continue
            if result is None or result.status is JobStatus.FAILED:
                logger.warning(
                    "tools worker: failed to seed %r (%s)",
                    spec["name"],
                    None if result is None else result.error,
                )
                continue
            seeded += 1
        if seeded:
            logger.info("tools worker: seeded %d builtin tool(s)", seeded)

    def _listed_names(self) -> set[str]:
        board = self._board(ListToolsJob)
        job_id = board.publish(ListToolsJob(include_disabled=True))
        result = _job_result(board, job_id)
        if result is None or result.status is not JobStatus.COMPLETED:
            return set()
        return {tool.name for tool in result.tools}

    async def _on_run(self, job: RunToolJob) -> None:
        try:
            await self._fail(job, "tool execution is not attached")
        except asyncio.CancelledError:
            await self._fail(job, "tools worker cancelled")
            raise
        except Exception:  # noqa: BLE001 -- no job can kill the worker
            logger.exception("tools worker: unhandled exception on job %s", job.id)
            await self._fail(job, "tool execution is not attached")

    async def _fail(self, job: RunToolJob, error: str) -> None:
        result = RunToolResult(
            id=job.id,
            status=JobStatus.FAILED,
            error=error,
        )
        if not await self.call(self._board(RunToolJob).submit_result, result):
            logger.warning("tools worker: failed to submit result for %s", job.id)


def _job_result(board, job_id: int):
    """Wait until an OperateBook Job leaves PREPARING and has a result."""
    deadline = time.monotonic() + _RESULT_TIMEOUT
    while time.monotonic() < deadline:
        result = board.get_result(job_id)
        if result is not None:
            return result
        time.sleep(0.01)
    return None

