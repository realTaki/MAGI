"""Independent, serial Agent processing for one conversation."""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from functools import partial
from typing import TYPE_CHECKING, Any

from bus import (
    CallLLMJob,
    CallLLMResult,
    ChatNotify,
    ChatNotifyResult,
    DeliveryNotify,
    JobStatus,
    LLMMessage,
    LLMMessageRole,
    LLMToolCall,
    RunToolJob,
    go,
)

from .context import AgentContext

if TYPE_CHECKING:
    from .worker import AgentWorker


@dataclass
class RunContext:
    """Mutable state belonging to one run of this conversation."""

    conversation_id: int
    contact_id: int
    messages: list[LLMMessage] = field(default_factory=list)
    jobs: list[ChatNotify] = field(default_factory=list)
    final_reply: str = ""
    failed: bool = False


class Conversation:
    """Own a conversation's queue and serial LLM/tool execution."""

    def __init__(self, worker: AgentWorker, conversation_id: int) -> None:
        self._worker = worker
        self.conversation_id = conversation_id
        self._context = AgentContext(worker, conversation_id=conversation_id)
        self._pending: deque[ChatNotify] = deque()
        self._running = False

    def submit(self, job: ChatNotify) -> None:
        """Queue a claimed turn without running two turns for this conversation."""
        self._pending.append(job)
        if self._running:
            return
        self._running = True
        go(self._run())

    async def _run(self) -> None:
        try:
            while self._pending:
                await self._run_turn(self._pending.popleft())
        finally:
            self._running = False
            self._worker._release_conversation(self)

    async def _run_turn(self, job: ChatNotify) -> None:
        ctx = RunContext(
            conversation_id=job.conversation_id,
            contact_id=job.contact_id,
            jobs=[job],
        )
        try:
            await self._process(ctx)
        except BaseException as exc:  # noqa: BLE001 -- every turn failure is durable
            ctx.failed = True
            ctx.final_reply = f"处理请求时发生错误：{exc}"
            self._deliver(ctx)
        finally:
            status = JobStatus.FAILED if ctx.failed else JobStatus.COMPLETED
            for submitted in ctx.jobs:
                self._worker.submit(
                    ChatNotify,
                    ChatNotifyResult(
                        id=submitted.id,
                        status=status,
                        error=ctx.final_reply if ctx.failed else None,
                    ),
                )

    async def _process(self, ctx: RunContext) -> None:
        if not await self._context.get(ctx.contact_id):
            ctx.failed = True
            ctx.final_reply = "会话不存在。"
            return

        record = self._context.conversation
        assert record is not None
        llm_timeout = await self._context.setting_float("llm_timeout_seconds", 120)
        ctx.messages = await self._context.compact(
            summary=record.summary,
            history=self._context.history,
            records=self._context.records,
            call_llm=partial(self._call_llm, timeout=llm_timeout),
        )
        max_tokens = await self._context.setting_int("max_tokens", 1024)
        tool_wait_seconds = await self._context.setting_float("tool_wait_seconds", 300)

        while True:
            llm = await self._call_llm(
                [
                    LLMMessage(role=LLMMessageRole.SYSTEM, content=self._context.system),
                    *ctx.messages,
                ],
                self._context.tools,
                max_tokens=max_tokens,
                timeout=llm_timeout,
            )
            if llm is None:
                ctx.failed = True
                ctx.final_reply = "回复生成超时，请稍后再试。"
                self._deliver(ctx)
                return
            if llm.status is not JobStatus.COMPLETED or llm.message is None:
                ctx.failed = True
                ctx.final_reply = llm.error or "回复生成失败。"
                self._deliver(ctx)
                return

            assistant = llm.message
            ctx.messages.append(assistant)
            if not assistant.tool_calls:
                ctx.final_reply = assistant.content
                self._deliver(ctx)
                return
            ctx.messages.extend(
                await self._run_tools(assistant.tool_calls, wait_seconds=tool_wait_seconds)
            )
            if self._pending:
                pending = self._pending.popleft()
                ctx.jobs.append(pending)
                ctx.messages.append(LLMMessage(role=LLMMessageRole.USER, content=pending.text))

    async def _run_tools(
        self,
        calls: list[LLMToolCall],
        *,
        wait_seconds: float,
    ) -> list[LLMMessage]:
        board = self._worker.board(RunToolJob)
        if board is None:
            return [
                LLMMessage(
                    role=LLMMessageRole.TOOL,
                    tool_call_id=call.tool_call_id,
                    content="tool board is not mounted",
                    is_error=True,
                )
                for call in calls
            ]
        pending = {
            call.tool_call_id: (
                call,
                await self._worker.call(
                    board.publish,
                    RunToolJob(publisher=self._worker.worker_name, call=call),
                ),
            )
            for call in calls
        }
        results: dict[str, Any] = {}
        deadline = asyncio.get_running_loop().time() + wait_seconds
        while pending and asyncio.get_running_loop().time() < deadline:
            for call_id, (_, job_id) in tuple(pending.items()):
                status = await self._worker.call(board.check_job_status, job_id)
                if status not in {JobStatus.COMPLETED, JobStatus.FAILED}:
                    continue
                result = await self._worker.call(board.get_result, job_id, timeout=0)
                if result is not None:
                    results[call_id] = result
                    del pending[call_id]
            if pending:
                await asyncio.sleep(0.1)
        messages: list[LLMMessage] = []
        for call_id in pending:
            results[call_id] = None
        for call in calls:
            result = results.get(call.tool_call_id)
            failed = result is None or result.status is not JobStatus.COMPLETED
            content = (
                "tool execution timed out"
                if result is None
                else (result.error if failed else result.content)
            )
            messages.append(
                LLMMessage(
                    role=LLMMessageRole.TOOL,
                    tool_call_id=call.tool_call_id,
                    content=content or "tool execution failed",
                    is_error=failed,
                )
            )
        return messages

    async def _call_llm(
        self,
        messages: list[LLMMessage],
        tools,
        *,
        max_tokens: int,
        timeout: float,
    ) -> CallLLMResult | None:
        board = self._worker.board(CallLLMJob)
        if board is None:
            return CallLLMResult(status=JobStatus.FAILED, error="LLM board is not mounted")
        job_id = await self._worker.call(
            board.publish,
            CallLLMJob(
                publisher=self._worker.worker_name,
                messages=messages,
                tools=tools,
                max_tokens=max_tokens,
            ),
        )
        return await self._worker.call(board.get_result, job_id, timeout=timeout)

    def _deliver(self, ctx: RunContext) -> None:
        self._worker.publish_notify(
            DeliveryNotify(
                publisher=self._worker.worker_name,
                conversation_id=ctx.conversation_id,
                text=ctx.final_reply or "处理完毕。",
            )
        )
