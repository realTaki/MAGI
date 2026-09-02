"""Tools worker: seed the catalog, then claim RunToolJob.

Attach upserts builtin catalog rows through ``SetToolsJob`` and drops
retired names. The loop claims ``RunToolJob`` and dispatches ``run``.
"""

from __future__ import annotations

from bus import (
    BaseWorker,
    DeleteToolJob,
    JobStatus,
    LLMTool,
    ListToolsJob,
    RunToolJob,
    RunToolResult,
    SetToolsJob,
    Tool,
    go,
)
from tools.registry import RETIRED_BUILTIN_NAMES, builtin_catalog, configure, get_tool


class ToolsWorker(BaseWorker):
    worker_name = "tools"

    async def on_attached(self) -> None:
        configure(bus=self.bus)
        listed = await self.ask(ListToolsJob(include_disabled=True, publisher=self.worker_name))
        existing = {tool.definition.name for tool in listed.tools or []} if listed is not None else set()
        catalog = builtin_catalog()
        await self.ask(
            SetToolsJob(
                publisher=self.worker_name,
                tools=[
                    Tool(
                        name=spec["name"],
                        definition=LLMTool(
                            name=spec["name"],
                            description=spec["description"],
                            input_schema=spec["input_schema"],
                        ),
                    )
                    for spec in catalog
                ],
            )
        )
        for name in RETIRED_BUILTIN_NAMES:
            if name in existing:
                await self.ask(DeleteToolJob(publisher=self.worker_name, name=name))

    async def _poll(self) -> bool:
        job = await self.claim(RunToolJob)
        if job is None:
            return False
        go(self._on_run(job))
        return True

    async def _on_run(self, job: RunToolJob) -> None:
        call = job.call
        tool = get_tool(call.name)
        if tool is None:
            self._fail(job, f"unknown tool {call.name!r}")
            return
        try:
            outcome = await tool.run(**dict(call.arguments or {}))
        except Exception as exc:  # noqa: BLE001 -- tool bugs belong on RunToolResult
            self._fail(job, str(exc))
            return
        if outcome.is_error:
            self._fail(job, outcome.content)
            return
        self.submit(RunToolJob, RunToolResult(id=job.id, content=outcome.content))

    def _fail(self, job: RunToolJob, error: str) -> None:
        self.submit(
            RunToolJob,
            RunToolResult(id=job.id, status=JobStatus.FAILED, error=error),
        )
