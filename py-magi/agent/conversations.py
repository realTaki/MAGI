"""Serial processing and LLM context for one conversation."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from functools import partial
from itertools import chain
from typing import TYPE_CHECKING, Any

from bus import (
    ArchiveMessagesJob,
    CallLLMJob,
    CallLLMResult,
    ChatNotify,
    ChatNotifyResult,
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

from .compaction import (
    ToolRound,
    estimate_messages_tokens,
    estimate_string_tokens,
    estimate_tools_tokens,
    tool_rounds_messages,
    trim_tool_rounds,
)

if TYPE_CHECKING:
    from .worker import AgentWorker


@dataclass
class Conversation:
    """One conversation's serial queue, durable snapshot, and current LLM run."""

    _worker: AgentWorker
    conversation_id: int

    _pending: deque[ChatNotify] = field(init=False, default_factory=deque)
    _running: bool = field(init=False, default=False)

    conversation: Any | None = field(init=False, default=None)
    contact_id: int = field(init=False, default=0)

    # LLM message layout: SYSTEM → summary → history → retained text → tool rounds.
    system: str = field(init=False, default="")
    summary: str = field(init=False, default="")
    history: list[LLMMessage] = field(init=False, default_factory=list)
    records: list[Any] = field(init=False, default_factory=list)
    _history_loaded: bool = field(init=False, default=False)
    retained_text: list[LLMMessage] = field(init=False, default_factory=list)
    tool_rounds: tuple[ToolRound, ...] = field(init=False, default=())
    tools: list[Any] = field(init=False, default_factory=list)

    jobs: list[ChatNotify] = field(init=False, default_factory=list)
    final_reply: str = field(init=False, default="")
    failed: bool = field(init=False, default=False)
    _assistant: LLMMessage | None = field(init=False, default=None)

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
        self._begin_turn(job)
        try:
            if not await self._refresh():
                self._fail("会话不存在。")
                return
            await self._process()
        except BaseException as exc:  # noqa: BLE001 -- every turn failure is durable
            self._fail(f"处理请求时发生错误：{exc}")
            self._deliver()
        finally:
            self._settle()

    async def _process(self) -> None:
        llm_timeout = await self._setting_float("llm_timeout_seconds", 120)
        max_tokens = await self._setting_int("max_tokens", 1024)
        tool_wait_seconds = await self._setting_float("tool_wait_seconds", 300)

        while True:
            await self._compact(
                call_llm=partial(self._call_llm, timeout=llm_timeout),
                max_tokens=max_tokens,
            )
            llm = await self._call_llm(
                self._messages(),
                self.tools,
                max_tokens=max_tokens,
                timeout=llm_timeout,
            )
            if llm is None:
                self._fail("回复生成超时，请稍后再试。")
                self._deliver()
                return
            if llm.status is not JobStatus.COMPLETED or llm.message is None:
                self._fail(llm.error or "回复生成失败。")
                self._deliver()
                return

            if not self._add_llm(llm.message):
                self._deliver()
                return
            tool_messages = await self._run_tools(
                llm.message.tool_calls or [],
                wait_seconds=tool_wait_seconds,
            )
            pending = self._pending.popleft() if self._pending else None
            self._add_tools(tool_messages, pending)

    def _deliver(self) -> None:
        """Publish this turn's visible reply, then retain it in local history."""
        text = self.final_reply or "处理完毕。"
        self._worker.publish_notify(
            DeliveryNotify(
                publisher=self._worker.worker_name,
                conversation_id=self.conversation_id,
                text=text,
            )
        )
        self._commit_reply()

    def _begin_turn(self, job: ChatNotify) -> None:
        self.jobs = [job]
        self.contact_id = job.contact_id
        if self._history_loaded:
            self.history.append(LLMMessage(role=LLMMessageRole.USER, content=job.text))
        self.tool_rounds = ()
        self.retained_text = []
        self.final_reply = ""
        self.failed = False
        self._assistant = None

    async def _refresh(self) -> bool:
        """Refresh external dependencies; load the message snapshot only once."""
        conversation = await self._conversation()
        if conversation is None:
            return False
        self.conversation = conversation
        self.summary = conversation.summary
        if not self._history_loaded:
            self.history, self.records = await self._history()
            self._history_loaded = True
        self.system = await self._system_prompt(
            conversation_instruction=conversation.instruction,
            conversation_info=conversation.info,
        )
        self.tools = await self._tools()
        return True

    def _messages(self) -> list[LLMMessage]:
        """Return the next LLM input in its protocol order."""
        return list(
            chain(
                (LLMMessage(role=LLMMessageRole.SYSTEM, content=self.system),),
                self._summary_messages(),
                self.history,
                self.retained_text,
                tool_rounds_messages(self.tool_rounds),
            )
        )

    async def _compact(
        self,
        *,
        call_llm: Callable[..., Awaitable[CallLLMResult | None]],
        max_tokens: int,
    ) -> None:
        """Summarize durable history when the next complete payload exceeds budget."""
        if self._estimated_payload_tokens(max_tokens=max_tokens) <= await self._context_threshold():
            return

        keep = max(1, await self._setting_int("compact_keep_recent", 20))
        tail_records = self.records[-keep:]
        if len(tail_records) == len(self.records):
            return
        source_messages = [
            *self._summary_messages(),
            *self.history[: len(self.history) - len(tail_records)],
        ]
        prompt = await self._prompt("agent/compaction")
        if not prompt:
            return
        result = await call_llm(
            [
                LLMMessage(role=LLMMessageRole.SYSTEM, content=prompt),
                LLMMessage(
                    role=LLMMessageRole.USER,
                    content="\n\n".join(
                        f"[{message.role.value.upper()}]\n{message.content}"
                        for message in source_messages
                    ),
                ),
            ],
            [],
            max_tokens=1024,
        )
        if result is None or result.status is not JobStatus.COMPLETED or result.message is None:
            return
        summary = result.message.content.strip()
        if not summary:
            return
        updated = await self._worker.ask(
            UpdateConversationSummaryJob(
                publisher=self._worker.worker_name,
                conversation_id=self.conversation_id,
                summary=summary,
            )
        )
        if updated is None:
            return
        await self._worker.ask(
            ArchiveMessagesJob(
                publisher=self._worker.worker_name,
                conversation_id=self.conversation_id,
                before_message_id=tail_records[0].id,
            )
        )
        self.summary = summary
        self.history = self._messages_from_records(tail_records)
        self.records = tail_records
        if self.conversation is not None:
            self.conversation.summary = summary

    def _add_llm(self, message: LLMMessage) -> bool:
        self._assistant = message
        if message.tool_calls:
            return True
        self.final_reply = message.content
        return False

    def _add_tools(self, results: list[LLMMessage], pending: ChatNotify | None) -> None:
        assistant = self._assistant
        assert assistant is not None and assistant.tool_calls
        next_input = None
        if pending is not None:
            self.jobs.append(pending)
            next_input = LLMMessage(role=LLMMessageRole.USER, content=pending.text)
        rounds, retained_text = trim_tool_rounds(
            (*self.tool_rounds, ToolRound(assistant, tuple(results), next_input)),
            keep=2,
        )
        self.tool_rounds = rounds
        self.retained_text.extend(retained_text)
        self._assistant = None

    def _fail(self, error: str) -> None:
        self.failed = True
        self.final_reply = error

    def _commit_reply(self) -> None:
        """Move this run's visible input/output into the local history."""
        for round_ in self.tool_rounds:
            if round_.pending is not None:
                self.history.append(round_.pending)
        if self.final_reply:
            self.history.append(LLMMessage(role=LLMMessageRole.ASSISTANT, content=self.final_reply))
        self.tool_rounds = ()
        self.retained_text = []

    def _settle(self) -> None:
        status = JobStatus.FAILED if self.failed else JobStatus.COMPLETED
        for job in self.jobs:
            self._worker.submit(
                ChatNotify,
                ChatNotifyResult(
                    id=job.id,
                    status=status,
                    error=self.final_reply if self.failed else None,
                ),
            )

    async def _conversation(self):
        result = await self._worker.ask(
            GetConversationJob(
                publisher=self._worker.worker_name,
                conversation_id=self.conversation_id,
            )
        )
        return None if result is None else result.conversation

    async def _history(self) -> tuple[list[LLMMessage], list]:
        result = await self._worker.ask(
            ListConversationMessagesJob(
                publisher=self._worker.worker_name,
                conversation_id=self.conversation_id,
            )
        )
        records = [] if result is None or result.messages is None else result.messages
        return self._messages_from_records(records), records

    async def _system_prompt(
        self,
        *,
        conversation_instruction: str | None,
        conversation_info: str | None,
    ) -> str:
        soul = await self._prompt("agent/soul") or "You are a helpful assistant."
        instruction = self._instruction_block(await self._setting("instruction"))
        skills = await self._skills()
        memories_result = await self._worker.ask(
            ListMemoriesJob(publisher=self._worker.worker_name)
        )
        memories = [] if memories_result is None else memories_result.memories or []
        contact_result = await self._worker.ask(
            GetContactJob(publisher=self._worker.worker_name, contact_id=self.contact_id)
        )
        contact = None if contact_result is None else contact_result.contact
        notes_result = await self._worker.ask(
            ListContactNotesJob(
                publisher=self._worker.worker_name,
                contact_id=self.contact_id,
                kind=NoteKind.PERMANENT,
            )
        )
        notes = [] if notes_result is None else notes_result.contact_notes or []
        daily_result = await self._worker.ask(
            ListContactNotesJob(
                publisher=self._worker.worker_name,
                contact_id=self.contact_id,
                kind=NoteKind.DAILY,
            )
        )
        daily_note = (
            None
            if daily_result is None or not daily_result.contact_notes
            else daily_result.contact_notes[0].note
        )
        return self._format_system(
            soul=soul,
            instruction=instruction,
            skills=skills,
            memories=memories,
            contact=contact,
            notes=notes,
            conversation_instruction=conversation_instruction,
            conversation_info=conversation_info,
            daily_note=daily_note,
        )

    async def _tools(self) -> list[Any]:
        result = await self._worker.ask(ListToolsJob(publisher=self._worker.worker_name))
        return (
            []
            if result is None or result.tools is None
            else [tool.definition for tool in result.tools]
        )

    async def _context_threshold(self) -> int:
        window = await self._setting_int("compact_context_window", 100_000)
        percent = await self._setting_int("compact_threshold_pct", 80)
        return window * percent // 100

    def _estimated_payload_tokens(self, *, max_tokens: int) -> int:
        return (
            estimate_string_tokens(self.system)
            + estimate_messages_tokens(self._summary_messages())
            + estimate_messages_tokens(self.history)
            + estimate_messages_tokens(self.retained_text)
            + estimate_messages_tokens(tool_rounds_messages(self.tool_rounds))
            + estimate_tools_tokens(self.tools)
            + max_tokens
        )

    def _summary_messages(self) -> tuple[LLMMessage, ...]:
        if not self.summary:
            return ()
        return (
            LLMMessage(
                role=LLMMessageRole.USER,
                content=f"[Prior conversation summary]\n{self.summary}",
            ),
        )

    @staticmethod
    def _messages_from_records(records) -> list[LLMMessage]:
        return [
            LLMMessage(
                role=LLMMessageRole.ASSISTANT if record.contact_id == 1 else LLMMessageRole.USER,
                content=record.content,
            )
            for record in records
        ]

    @staticmethod
    def _instruction_block(value: str | None) -> str:
        instruction = (value or "").strip()
        if not instruction:
            return ""
        return (
            "# Instructions\n"
            "These instructions are part of your operating context. Try to comply with all of them. "
            "If they conflict irreconcilably, explain the conflict instead of silently choosing one.\n\n"
            "## Your personal instruction\n" + instruction
        )

    @staticmethod
    def _format_system(
        *,
        soul: str,
        instruction: str,
        skills: list[str],
        memories,
        contact,
        notes,
        conversation_instruction: str | None,
        conversation_info: str | None,
        daily_note: str | None,
    ) -> str:
        system = soul
        if instruction:
            system += "\n\n" + instruction
        if skills:
            system += "\n\n## Available skills\n" + "\n".join(f"- {name}" for name in skills)
        if memories:
            system += "\n\n## Long-term memory\n" + "\n".join(
                f"- {memory.topic}: {memory.detail}" for memory in memories
            )
        if contact is not None:
            block = f"## Current chatter\nName: {contact.nickname or contact.name or 'Unknown'}"
            for note in notes:
                if note.note:
                    block += f"\n- {note.note}"
            system += "\n\n" + block
        if conversation_instruction:
            system += "\n\n## Conversation instruction\n" + conversation_instruction
        if conversation_info:
            system += "\n\n## Conversation info\n" + conversation_info
        if daily_note:
            system += "\n\n## Daily note\n" + daily_note
        return system.strip() or soul

    async def _prompt(self, key: str) -> str | None:
        result = await self._worker.ask(GetPromptJob(publisher=self._worker.worker_name, key=key))
        return None if result is None else result.value

    async def _skills(self) -> list[str]:
        listed = await self._worker.ask(ListSkillsJob(publisher=self._worker.worker_name))
        if listed is None or not listed.names:
            return []
        return [
            name
            for name in listed.names
            if await self._worker.ask(GetSkillJob(publisher=self._worker.worker_name, name=name))
            is not None
        ]

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
        tools: list[Any],
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


__all__ = ["Conversation"]
