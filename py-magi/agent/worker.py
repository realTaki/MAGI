"""Agent Worker: durable turns in, durable LLM/tool/delivery Jobs out."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from bus import (
    ArchiveMessagesJob,
    BaseWorker,
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
    RegisterPromptJob,
    RunToolJob,
    UpdateConversationSummaryJob,
    go,
)

from .context import format_system_prompt, messages_from_records, render_instruction_block
from .compaction import estimate_messages_tokens, estimate_string_tokens
from .prompt_defaults import prompt_defaults

logger = logging.getLogger("agent.worker")


@dataclass
class RunContext:
    conversation_id: int
    contact_id: int
    messages: list[LLMMessage] = field(default_factory=list)
    final_reply: str = ""
    failed: bool = False


class AgentWorker(BaseWorker):
    """Consume ``ChatNotify`` while keeping one active turn per conversation."""

    worker_name = "agent"
    default_settings = {
        "max_iterations": "10",
        "max_tokens": "1024",
        "tool_wait_seconds": "300",
        "llm_timeout_seconds": "120",
        "compact_keep_recent": "20",
        "compact_context_window": "100000",
        "compact_threshold_pct": "80",
    }

    def __init__(self, bus, *, poll_seconds: float = 0.25, concurrency: int = 4) -> None:
        super().__init__(bus, poll_seconds=poll_seconds)
        self.concurrency = max(1, concurrency)
        self._active_conversations: set[int] = set()
        self._active_count = 0

    async def on_attached(self) -> None:
        for key, value in prompt_defaults():
            await self.ask(RegisterPromptJob(publisher=self.worker_name, key=key, value=value))

    async def _poll(self) -> bool:
        if self._active_count >= self.concurrency:
            return False
        board = self.board(ChatNotify)
        if board is None:
            return False
        job = await self.call(
            board.claim_for_new_conversation,
            active_conversation_ids=self._active_conversations,
        )
        if job is None:
            return False
        self._active_count += 1
        self._active_conversations.add(job.conversation_id)
        go(self._run_turn(job))
        return True

    async def _run_turn(self, job: ChatNotify) -> None:
        ctx = RunContext(conversation_id=job.conversation_id, contact_id=job.contact_id)
        try:
            await self._process(ctx)
        except asyncio.CancelledError:
            ctx.failed = True
            raise
        except Exception as exc:  # noqa: BLE001 -- terminal failure belongs on ChatNotify
            logger.exception("agent turn failed conversation=%s", job.conversation_id)
            ctx.failed = True
            ctx.final_reply = f"处理请求时发生错误：{exc}"
            await self._deliver(ctx)
        finally:
            status = JobStatus.FAILED if ctx.failed else JobStatus.COMPLETED
            self.submit(ChatNotify, ChatNotifyResult(id=job.id, status=status))
            self._active_conversations.discard(job.conversation_id)
            self._active_count -= 1

    async def _process(self, ctx: RunContext) -> None:
        conversation = await self._conversation(ctx.conversation_id)
        if conversation is None:
            ctx.failed = True
            ctx.final_reply = "会话不存在。"
            await self._deliver(ctx)
            return

        history, source_messages = await self._history(ctx.conversation_id, conversation.summary)
        ctx.messages = await self._maybe_compact(ctx, conversation.summary, history, source_messages)
        system = await self._system_prompt(ctx.contact_id, conversation.instruction, conversation.info)
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
                await self._deliver(ctx)
                return
            if llm.status is not JobStatus.COMPLETED or llm.message is None:
                ctx.failed = True
                ctx.final_reply = llm.error or "回复生成失败。"
                await self._deliver(ctx)
                return

            assistant = llm.message
            ctx.messages.append(assistant)
            if not assistant.tool_calls:
                ctx.final_reply = assistant.content
                await self._deliver(ctx)
                return
            ctx.messages.extend(await self._run_tools(ctx, assistant.tool_calls))

        ctx.final_reply = "已达到最大工具调用次数，请简化你的请求。"
        await self._deliver(ctx)

    async def _conversation(self, conversation_id: int):
        result = await self.ask(
            GetConversationJob(publisher=self.worker_name, conversation_id=conversation_id)
        )
        return None if result is None else result.conversation

    async def _history(self, conversation_id: int, summary: str) -> tuple[list[LLMMessage], list]:
        result = await self.ask(
            ListConversationMessagesJob(publisher=self.worker_name, conversation_id=conversation_id)
        )
        records = [] if result is None or result.messages is None else result.messages
        return messages_from_records(summary=summary, records=records), records

    async def _system_prompt(
        self, contact_id: int, conversation_instruction: str | None, conversation_info: str | None
    ) -> str:
        soul = await self._prompt("agent/soul") or "You are a helpful assistant."
        instruction = render_instruction_block(await self._setting("instruction"))
        skills = await self._skills()
        memories_result = await self.ask(ListMemoriesJob(publisher=self.worker_name))
        memories = [] if memories_result is None or memories_result.memories is None else memories_result.memories
        contact_result = await self.ask(GetContactJob(publisher=self.worker_name, contact_id=contact_id))
        contact = None if contact_result is None else contact_result.contact
        notes_result = await self.ask(
            ListContactNotesJob(
                publisher=self.worker_name, contact_id=contact_id, kind=NoteKind.PERMANENT
            )
        )
        notes: list[ContactNote] = (
            [] if notes_result is None or notes_result.contact_notes is None else notes_result.contact_notes
        )
        daily_result = await self.ask(
            ListContactNotesJob(publisher=self.worker_name, contact_id=contact_id, kind=NoteKind.DAILY)
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
        result = await self.ask(ListToolsJob(publisher=self.worker_name))
        return [] if result is None or result.tools is None else [tool.definition for tool in result.tools]

    async def _run_tools(self, ctx: RunContext, calls: list[LLMToolCall]) -> list[LLMMessage]:
        board = self.board(RunToolJob)
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
            call.tool_call_id: (call, await self.call(board.publish, RunToolJob(publisher=self.worker_name, call=call)))
            for call in calls
        }
        results: dict[str, Any] = {}
        deadline = asyncio.get_running_loop().time() + await self._setting_float("tool_wait_seconds", 300)
        chat_board = self.board(ChatNotify)
        while pending and asyncio.get_running_loop().time() < deadline:
            if chat_board is not None:
                steering = await self.call(
                    chat_board.claim_for_steering, conversation_id=ctx.conversation_id
                )
                if steering is not None:
                    ctx.messages.append(LLMMessage(role=LLMMessageRole.USER, content=steering.text))
                    self.submit(ChatNotify, ChatNotifyResult(id=steering.id))
            for call_id, (_, job_id) in tuple(pending.items()):
                status = await self.call(board.check_job_status, job_id)
                if status not in {JobStatus.COMPLETED, JobStatus.FAILED}:
                    continue
                result = await self.call(board.get_result, job_id, timeout=0)
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
            content = "tool execution timed out" if result is None else (result.error if failed else result.content)
            messages.append(
                LLMMessage(
                    role=LLMMessageRole.TOOL,
                    tool_call_id=call.tool_call_id,
                    content=content or "tool execution failed",
                    is_error=failed,
                )
            )
        return messages

    async def _call_llm(self, messages: list[LLMMessage], tools, *, max_tokens: int) -> CallLLMResult | None:
        board = self.board(CallLLMJob)
        if board is None:
            return CallLLMResult(status=JobStatus.FAILED, error="LLM board is not mounted")
        job_id = await self.call(
            board.publish,
            CallLLMJob(
                publisher=self.worker_name,
                messages=messages,
                tools=tools,
                max_tokens=max_tokens,
            ),
        )
        return await self.call(
            board.get_result,
            job_id,
            timeout=await self._setting_float("llm_timeout_seconds", 120),
        )

    async def _deliver(self, ctx: RunContext) -> None:
        self.publish_notify(
            DeliveryNotify(
                publisher=self.worker_name,
                conversation_id=ctx.conversation_id,
                text=ctx.final_reply or "处理完毕。",
            )
        )

    async def _maybe_compact(self, ctx: RunContext, summary: str, history: list[LLMMessage], records: list) -> list[LLMMessage]:
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
            f"[{message.role.value.upper()}]\n{message.content}" for message in history[:-len(tail_records)]
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
        updated = await self.ask(
            UpdateConversationSummaryJob(
                publisher=self.worker_name,
                conversation_id=ctx.conversation_id,
                summary=new_summary,
            )
        )
        if updated is None:
            return history
        await self.ask(
            ArchiveMessagesJob(
                publisher=self.worker_name,
                conversation_id=ctx.conversation_id,
                before_message_id=tail_records[0].id,
            )
        )
        return messages_from_records(summary=new_summary, records=tail_records)

    async def _prompt(self, key: str) -> str | None:
        result = await self.ask(GetPromptJob(publisher=self.worker_name, key=key))
        return None if result is None else result.value

    async def _skills(self) -> list[str]:
        listed = await self.ask(ListSkillsJob(publisher=self.worker_name))
        if listed is None or not listed.names:
            return []
        found: list[str] = []
        for name in listed.names:
            if await self.ask(GetSkillJob(publisher=self.worker_name, name=name)) is not None:
                found.append(name)
        return found

    async def _setting(self, key: str) -> str | None:
        result = await self.ask(GetSettingJob(publisher=self.worker_name, key=key))
        if result is not None and result.value is not None:
            return result.value
        result = await self.ask(GetSettingJob(publisher=self.worker_name, key=f"agent.{key}"))
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


async def submit_agent_message(bus, message: Any) -> int | None:
    """Compatibility entrypoint for adapters that publish one incoming turn."""
    board = bus.board(ChatNotify)
    if board is None:
        return None
    return await asyncio.to_thread(
        board.publish,
        ChatNotify(
            publisher="agent",
            conversation_id=getattr(message, "conversation_id", 0),
            contact_id=getattr(message, "contact_id", 0),
            text=getattr(message, "text", ""),
        ),
    )
