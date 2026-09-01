"""Independent, serial Agent processing for one conversation."""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from bus import (
    ArchiveMessagesJob,
    CallLLMJob,
    CallLLMResult,
    ChatNotify,
    ChatNotifyResult,
    ContactNote,
    DeliveryNotify,
    GetContactJob,
    GetConversationJob,
    GetPromptJob,
    GetSettingJob,
    GetSkillJob,
    JobStatus,
    ListContactNotesJob,
    ListConversationMessagesJob,
    ListMemoriesJob,
    ListSkillsJob,
    ListToolsJob,
    LLMMessage,
    LLMMessageRole,
    LLMToolCall,
    NoteKind,
    RunToolJob,
    UpdateConversationSummaryJob,
    go,
)

from .compaction import estimate_messages_tokens, estimate_string_tokens
from .context import format_system_prompt, messages_from_records, render_instruction_block

if TYPE_CHECKING:
    from .worker import AgentWorker


logger = logging.getLogger("agent.conversations")


@dataclass
class RunContext:
    """Mutable state belonging to one turn of this conversation."""

    conversation_id: int
    contact_id: int
    messages: list[LLMMessage] = field(default_factory=list)
    jobs: list[ChatNotify] = field(default_factory=list)
    final_reply: str = ""
    failed: bool = False


class Conversation:
    """Own a conversation's queued turns and all of their processing steps."""

    def __init__(self, worker: AgentWorker, conversation_id: int) -> None:
        self._worker = worker
        self.conversation_id = conversation_id
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
            logger.exception("agent turn failed conversation=%s", job.conversation_id)
            ctx.failed = True
            ctx.final_reply = f"处理请求时发生错误：{exc}"
            self._deliver(ctx)
        finally:
            status = JobStatus.FAILED if ctx.failed else JobStatus.COMPLETED
            for submitted in ctx.jobs:
                self._worker.submit(ChatNotify, ChatNotifyResult(id=submitted.id, status=status))

    async def _process(self, ctx: RunContext) -> None:
        record = await self._conversation(ctx.conversation_id)
        if record is None:
            ctx.failed = True
            ctx.final_reply = "会话不存在。"
            self._deliver(ctx)
            return

        history, source_messages = await self._history(ctx.conversation_id, record.summary)
        ctx.messages = await self._maybe_compact(ctx, record.summary, history, source_messages)
        system = await self._system_prompt(ctx.contact_id, record.instruction, record.info)
        tools = await self._tools()

        for _ in range(await self._setting_int("max_iterations", 10)):
            llm = await self._call_llm(
                [LLMMessage(role=LLMMessageRole.SYSTEM, content=system), *ctx.messages],
                tools,
                max_tokens=await self._setting_int("max_tokens", 1024),
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
            ctx.messages.extend(await self._run_tools(assistant.tool_calls))
            if self._pending:
                pending = self._pending.popleft()
                ctx.jobs.append(pending)
                ctx.messages.append(LLMMessage(role=LLMMessageRole.USER, content=pending.text))

        ctx.final_reply = "已达到最大工具调用次数，请简化你的请求。"
        self._deliver(ctx)

    async def _conversation(self, conversation_id: int):
        result = await self._worker.ask(
            GetConversationJob(
                publisher=self._worker.worker_name,
                conversation_id=conversation_id,
            )
        )
        return None if result is None else result.conversation

    async def _history(self, conversation_id: int, summary: str) -> tuple[list[LLMMessage], list]:
        result = await self._worker.ask(
            ListConversationMessagesJob(
                publisher=self._worker.worker_name,
                conversation_id=conversation_id,
            )
        )
        records = [] if result is None or result.messages is None else result.messages
        return messages_from_records(summary=summary, records=records), records

    async def _system_prompt(
        self,
        contact_id: int,
        conversation_instruction: str | None,
        conversation_info: str | None,
    ) -> str:
        soul = await self._prompt("agent/soul") or "You are a helpful assistant."
        instruction = render_instruction_block(await self._setting("instruction"))
        skills = await self._skills()
        memories_result = await self._worker.ask(
            ListMemoriesJob(publisher=self._worker.worker_name)
        )
        memories = (
            []
            if memories_result is None or memories_result.memories is None
            else memories_result.memories
        )
        contact_result = await self._worker.ask(
            GetContactJob(publisher=self._worker.worker_name, contact_id=contact_id)
        )
        contact = None if contact_result is None else contact_result.contact
        notes_result = await self._worker.ask(
            ListContactNotesJob(
                publisher=self._worker.worker_name,
                contact_id=contact_id,
                kind=NoteKind.PERMANENT,
            )
        )
        notes: list[ContactNote] = (
            []
            if notes_result is None or notes_result.contact_notes is None
            else notes_result.contact_notes
        )
        daily_result = await self._worker.ask(
            ListContactNotesJob(
                publisher=self._worker.worker_name,
                contact_id=contact_id,
                kind=NoteKind.DAILY,
            )
        )
        daily = (
            None
            if daily_result is None or not daily_result.contact_notes
            else daily_result.contact_notes[0].note
        )
        return format_system_prompt(
            soul=soul,
            instruction=instruction,
            skills=skills,
            memories=memories,
            contact=contact,
            notes=notes,
            daily_note=daily,
            conversation_instruction=conversation_instruction,
            conversation_info=conversation_info,
        )

    async def _tools(self):
        result = await self._worker.ask(ListToolsJob(publisher=self._worker.worker_name))
        return (
            []
            if result is None or result.tools is None
            else [tool.definition for tool in result.tools]
        )

    async def _run_tools(self, calls: list[LLMToolCall]) -> list[LLMMessage]:
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
        deadline = asyncio.get_running_loop().time() + await self._setting_float(
            "tool_wait_seconds", 300
        )
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
        return await self._worker.call(
            board.get_result,
            job_id,
            timeout=await self._setting_float("llm_timeout_seconds", 120),
        )

    def _deliver(self, ctx: RunContext) -> None:
        self._worker.publish_notify(
            DeliveryNotify(
                publisher=self._worker.worker_name,
                conversation_id=ctx.conversation_id,
                text=ctx.final_reply or "处理完毕。",
            )
        )

    async def _maybe_compact(
        self,
        ctx: RunContext,
        summary: str,
        history: list[LLMMessage],
        records: list,
    ) -> list[LLMMessage]:
        window = await self._setting_int("compact_context_window", 100_000)
        threshold = window * await self._setting_int("compact_threshold_pct", 80) // 100
        if estimate_string_tokens(summary) + estimate_messages_tokens(history) <= threshold:
            return history
        keep = max(1, await self._setting_int("compact_keep_recent", 20))
        tail_records = records[-keep:]
        to_archive = records[: len(records) - len(tail_records)]
        if not to_archive:
            return history
        prompt = await self._prompt("agent/compaction")
        if not prompt:
            return history
        source = "\n\n".join(
            f"[{message.role.value.upper()}]\n{message.content}"
            for message in history[: -len(tail_records)]
        )
        result = await self._call_llm(
            [
                LLMMessage(role=LLMMessageRole.SYSTEM, content=prompt),
                LLMMessage(role=LLMMessageRole.USER, content=source),
            ],
            [],
            max_tokens=1024,
        )
        if result is None or result.status is not JobStatus.COMPLETED or result.message is None:
            return history
        new_summary = result.message.content.strip()
        if not new_summary:
            return history
        updated = await self._worker.ask(
            UpdateConversationSummaryJob(
                publisher=self._worker.worker_name,
                conversation_id=ctx.conversation_id,
                summary=new_summary,
            )
        )
        if updated is None:
            return history
        await self._worker.ask(
            ArchiveMessagesJob(
                publisher=self._worker.worker_name,
                conversation_id=ctx.conversation_id,
                before_message_id=tail_records[0].id,
            )
        )
        return messages_from_records(summary=new_summary, records=tail_records)

    async def _prompt(self, key: str) -> str | None:
        result = await self._worker.ask(GetPromptJob(publisher=self._worker.worker_name, key=key))
        return None if result is None else result.value

    async def _skills(self) -> list[str]:
        listed = await self._worker.ask(ListSkillsJob(publisher=self._worker.worker_name))
        if listed is None or not listed.names:
            return []
        found: list[str] = []
        for name in listed.names:
            if (
                await self._worker.ask(GetSkillJob(publisher=self._worker.worker_name, name=name))
                is not None
            ):
                found.append(name)
        return found

    async def _setting(self, key: str) -> str | None:
        result = await self._worker.ask(GetSettingJob(publisher=self._worker.worker_name, key=key))
        if result is not None and result.value is not None:
            return result.value
        result = await self._worker.ask(
            GetSettingJob(publisher=self._worker.worker_name, key=f"agent.{key}")
        )
        return None if result is None else result.value

    async def _setting_int(self, key: str, default: int) -> int:
        try:
            return int(await self._setting(key) or default)
        except ValueError:
            return default

    async def _setting_float(self, key: str, default: float) -> float:
        try:
            return float(await self._setting(key) or default)
        except ValueError:
            return default
