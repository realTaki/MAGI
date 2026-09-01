"""Independent, serial Agent processing for one conversation."""

from __future__ import annotations

import asyncio
from collections import deque
from functools import partial
from typing import TYPE_CHECKING, Any

from bus import (
    CallLLMJob,
    CallLLMResult,
    ChatNotify,
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
        self._context.add_chat(job)
        try:
            if not await self._context.refresh():
                self._context.fail("会话不存在。")
                return
            await self._process()
        except BaseException as exc:  # noqa: BLE001 -- every turn failure is durable
            self._context.fail(f"处理请求时发生错误：{exc}")
            self._context.deliver()
        finally:
            self._context.settle()

    async def _process(self) -> None:
        llm_timeout = await self._context.setting_float("llm_timeout_seconds", 120)
        max_tokens = await self._context.setting_int("max_tokens", 1024)
        tool_wait_seconds = await self._context.setting_float("tool_wait_seconds", 300)

        while True:
            await self._context.compact(
                call_llm=partial(self._call_llm, timeout=llm_timeout),
                max_tokens=max_tokens,
            )
            llm = await self._call_llm(
                self._context.messages(),
                self._context.tools,
                max_tokens=max_tokens,
                timeout=llm_timeout,
            )
            if llm is None:
                self._context.fail("回复生成超时，请稍后再试。")
                self._context.deliver()
                return
            if llm.status is not JobStatus.COMPLETED or llm.message is None:
                self._context.fail(llm.error or "回复生成失败。")
                self._context.deliver()
                return

            if not self._context.add_llm(llm.message):
                self._context.deliver()
                return
            tool_messages = await self._run_tools(
                llm.message.tool_calls or [],
                wait_seconds=tool_wait_seconds,
            )
            pending = self._pending.popleft() if self._pending else None
            self._context.add_tools(tool_messages, pending)

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
