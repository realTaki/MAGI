"""Tools worker: seed the catalog, then claim RunToolJob.

Attach publishes missing builtin rows through ``SetToolsJob``. The loop
claims ``RunToolJob`` and ``go()``s the handler. Builtin ``run``
implementations are not attached yet.
"""

from __future__ import annotations

from bus import (
    BaseWorker,
    JobStatus,
    LLMTool,
    ListToolsJob,
    RunToolJob,
    RunToolResult,
    SetToolsJob,
    Tool,
    go,
)
from tools.registry import builtin_catalog


class ToolsWorker(BaseWorker):
    worker_name = "tools"

    async def on_attached(self) -> None:
        listed = await self.ask(ListToolsJob(include_disabled=True, publisher=self.worker_name))
        existing = {tool.definition.name for tool in listed.tools or []} if listed is not None else set()
        tools = [
            Tool(
                name=spec["name"],
                definition=LLMTool(
                    name=spec["name"],
                    description=spec["description"],
                    input_schema=spec["input_schema"],
                ),
            )
            for spec in builtin_catalog()
            if spec["name"] not in existing
        ]
        if tools:
            await self.ask(SetToolsJob(publisher=self.worker_name, tools=tools))

    async def _poll(self) -> bool:
        job = await self.claim(RunToolJob)
        if job is None:
            return False
        go(self._on_run(job))
        return True

    async def _on_run(self, job: RunToolJob) -> None:
        try:
            self._fail(job, "tool execution is not attached")
        except Exception as exc:  # noqa: BLE001 -- no job can kill the worker
            self._fail(job, str(exc))

    def _fail(self, job: RunToolJob, error: str) -> None:
        self.submit(
            RunToolJob,
            RunToolResult(id=job.id, status=JobStatus.FAILED, error=error),
        )
