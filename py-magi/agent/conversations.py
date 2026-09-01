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
    MAGI_CONTACT_ID,
    ArchiveMessagesJob,
    CallLLMJob,
    CallLLMResult,
    ChatNotify,
    ChatNotifyResult,
    DeliveryNotify,
    GetConversationJob,
    GetPromptJob,
    GetSettingJob,
    JobStatus,
    ListConversationMessagesJob,
    ListMemoriesJob,
    ListSkillsJob,
    ListToolsJob,
    LLMMessage,
    LLMMessageRole,
    LLMToolCall,
    RunToolJob,
    Skill,
    UpdateConversationSummaryJob,
    go,
)

from .compaction import (
    ToolRound,
    compact_source_messages,
    estimate_messages_tokens,
    estimate_tools_tokens,
    tool_rounds_messages,
    trim_tool_rounds,
)

if TYPE_CHECKING:
    from .worker import AgentWorker


@dataclass
class Conversation:
    """One conversation's serial queue, durable snapshot, and current LLM run."""

    # Worker is this Conversation's only BUS gateway.
    _worker: AgentWorker
    # Stable durable Conversation id; also the key used by AgentWorker to route jobs here.
    conversation_id: int

    # Claimed ChatNotify jobs waiting for this Conversation's serial loop.
    _pending: deque[ChatNotify] = field(init=False, default_factory=deque)
    # Whether that serial loop has already been started.
    _running: bool = field(init=False, default=False)

    # Latest durable Conversation record; refreshed before each outer turn.
    conversation: Any | None = field(init=False, default=None)

    # SYSTEM message layout: soul → skills → memories → conversation metadata.
    # Base personality prompt.
    soul: str = field(init=False, default="")
    # Names and one-line descriptions of currently available skills.
    skills: list[Skill] = field(init=False, default_factory=list)
    # Header for the SYSTEM skills section; body listing is name + description only.
    skills_block: str = field(init=False, default="")
    # Global long-term memories available to the agent.
    memories: list[Any] = field(init=False, default_factory=list)
    # Conversation-specific instruction from the Conversation record.
    conversation_instruction: str = field(init=False, default="")
    # Conversation-specific metadata from the Conversation record.
    conversation_info: str = field(init=False, default="")

    # Remaining LLM message layout: summary → history → tool rounds.
    # Durable summary that replaces archived MessageBook history.
    summary: LLMMessage | None = field(init=False, default=None)
    # In-memory non-archived chat transcript, including text retained from old tool rounds.
    history: list[LLMMessage] = field(init=False, default_factory=list)
    # First active MessageBook id; compact archives everything before the next cut.
    active_from_id: int | None = field(init=False, default=None)
    # At most two newest complete assistant-tool-result exchanges for the current LLM run.
    tool_rounds: tuple[ToolRound, ...] = field(init=False, default=())
    # Current Agent-visible tool definitions passed to CallLLMJob.
    tools: list[Any] = field(init=False, default_factory=list)

    # All inbound jobs absorbed by this run, including one pending job taken after tools.
    jobs: list[ChatNotify] = field(init=False, default_factory=list)
    # Error text for ChatNotifyResult when this turn failed.
    final_reply: str = field(init=False, default="")
    # Determines whether every absorbed ChatNotify is settled COMPLETED or FAILED.
    failed: bool = field(init=False, default=False)
    # Most recent CallLLM assistant result, held until its tool results are attached.
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
            conversation = await self._conversation()
            if conversation is not None:
                self.conversation = conversation
                self.summary = self._summary_message(conversation.summary)
            active = await self._get_active_messages()
            self.active_from_id = None if not active else active[0].id
            self.history = self._messages_from_records(active)
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
            error = f"处理请求时发生错误：{exc}"
            self._fail(error)
            self._deliver(error)
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
                error = "回复生成超时，请稍后再试。"
                self._fail(error)
                self._deliver(error)
                return
            if llm.status is not JobStatus.COMPLETED or llm.message is None:
                error = llm.error or "回复生成失败。"
                self._fail(error)
                self._deliver(error)
                return

            if not self._add_llm(llm.message):
                self._deliver(llm.message.content)
                return
            tool_messages = await self._run_tools(
                llm.message.tool_calls or [],
                wait_seconds=tool_wait_seconds,
            )
            pending = self._pending.popleft() if self._pending else None
            self._add_tools(tool_messages, pending)

    def _deliver(self, text: str) -> None:
        """Publish this turn's visible reply, then retain it in local history."""
        text = text or "处理完毕。"
        self._worker.publish_notify(
            DeliveryNotify(
                publisher=self._worker.worker_name,
                conversation_id=self.conversation_id,
                text=text,
            )
        )
        self._commit_reply(text)

    def _begin_turn(self, job: ChatNotify) -> None:
        self.jobs = [job]
        self.history.append(LLMMessage(role=LLMMessageRole.USER, content=job.text))
        self.tool_rounds = ()
        self.final_reply = ""
        self.failed = False
        self._assistant = None

    async def _refresh(self) -> bool:
        """Refresh per-turn SYSTEM context and tools."""
        conversation = await self._conversation()
        if conversation is None:
            return False
        self.conversation = conversation
        await self._refresh_system(conversation)
        self.tools = await self._tools()
        return True

    def _messages(self) -> list[LLMMessage]:
        """Return the next LLM input in its protocol order."""
        return list(
            chain(
                (self._system_message(),),
                self._summary_messages(),
                self.history,
                tool_rounds_messages(self.tool_rounds),
            )
        )

    async def _compact(
        self,
        *,
        call_llm: Callable[..., Awaitable[CallLLMResult | None]],
        max_tokens: int,
    ) -> None:
        """Summarize durable conversation history when it exceeds budget."""
        if self._estimated_payload_tokens(max_tokens=max_tokens) <= await self._context_threshold():
            return

        keep = max(1, await self._setting_int("compact_keep_recent", 20))
        live = await self._get_active_messages()
        if len(live) <= keep:
            return
        cut_id = live[-keep].id
        overflow = len(self.history) - keep
        source_messages = compact_source_messages(
            (
                *self._summary_messages(),
                *(
                    self.history[:overflow]
                    if overflow > 0
                    else self._messages_from_records(live[:-keep])
                ),
            )
        )
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
                before_message_id=cut_id,
            )
        )
        self.summary = self._summary_message(summary)
        self.active_from_id = cut_id
        self.history = self._messages_from_records(live[-keep:])
        if self.conversation is not None:
            self.conversation.summary = summary

    def _add_llm(self, message: LLMMessage) -> bool:
        self._assistant = message
        return bool(message.tool_calls)

    def _add_tools(self, results: list[LLMMessage], pending: ChatNotify | None) -> None:
        assistant = self._assistant
        assert assistant is not None and assistant.tool_calls
        next_input = None
        if pending is not None:
            self.jobs.append(pending)
            next_input = LLMMessage(role=LLMMessageRole.USER, content=pending.text)
        rounds, expired_text = trim_tool_rounds(
            (*self.tool_rounds, ToolRound(assistant, tuple(results), next_input)),
            keep=2,
        )
        self.tool_rounds = rounds
        self.history.extend(expired_text)
        self._assistant = None

    def _fail(self, error: str) -> None:
        self.failed = True
        self.final_reply = error

    def _commit_reply(self, text: str) -> None:
        """Move this run's visible input/output into the local history."""
        for round_ in self.tool_rounds:
            if round_.pending is not None:
                self.history.append(round_.pending)
        if text:
            self.history.append(LLMMessage(role=LLMMessageRole.ASSISTANT, content=text))
        self.tool_rounds = ()

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

    async def _get_active_messages(self) -> list:
        result = await self._worker.ask(
            ListConversationMessagesJob(
                publisher=self._worker.worker_name,
                conversation_id=self.conversation_id,
            )
        )
        return [] if result is None or result.messages is None else result.messages

    async def _refresh_system(self, conversation) -> None:
        """Refresh the independently visible sections of the SYSTEM message."""
        self.soul = await self._prompt("agent/soul") or "You are a helpful assistant."
        self.skills = await self._skills()
        self.skills_block = (await self._prompt("agent/skills_block") or "").strip()
        memories_result = await self._worker.ask(
            ListMemoriesJob(publisher=self._worker.worker_name)
        )
        self.memories = [] if memories_result is None else memories_result.memories or []
        self.conversation_instruction = conversation.instruction or ""
        self.conversation_info = conversation.info or ""

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
        """Estimate durable conversation size. Temporary tool rounds are excluded."""
        return (
            estimate_messages_tokens((self._system_message(),))
            + estimate_messages_tokens(self._summary_messages())
            + estimate_messages_tokens(self.history)
            + estimate_tools_tokens(self.tools)
            + max_tokens
        )

    def _summary_messages(self) -> tuple[LLMMessage, ...]:
        return () if self.summary is None else (self.summary,)

    @staticmethod
    def _summary_message(text: str | None) -> LLMMessage | None:
        if not text:
            return None
        return LLMMessage(
            role=LLMMessageRole.USER,
            content=f"[Prior conversation summary]\n{text}",
        )

    @staticmethod
    def _messages_from_records(records) -> list[LLMMessage]:
        return [
            LLMMessage(
                role=LLMMessageRole.ASSISTANT
                if record.contact_id == MAGI_CONTACT_ID
                else LLMMessageRole.USER,
                content=record.content,
            )
            for record in records
        ]

    def _system_message(self) -> LLMMessage:
        """Render the declared SYSTEM sections in their field order."""
        sections = [self.soul]
        if self.skills:
            listing = "\n".join(f"- {skill.name}: {skill.description}" for skill in self.skills)
            header = self.skills_block or "## Available skills"
            sections.append(f"{header}\n{listing}")
        if self.memories:
            sections.append(
                "## Long-term memory\n"
                + "\n".join(f"- {memory.topic}: {memory.detail}" for memory in self.memories)
            )
        if self.conversation_instruction:
            sections.append("## Conversation instruction\n" + self.conversation_instruction)
        if self.conversation_info:
            sections.append("## Conversation info\n" + self.conversation_info)
        return LLMMessage(
            role=LLMMessageRole.SYSTEM,
            content="\n\n".join(sections).strip() or "You are a helpful assistant.",
        )

    async def _prompt(self, key: str) -> str | None:
        result = await self._worker.ask(GetPromptJob(publisher=self._worker.worker_name, key=key))
        return None if result is None else result.value

    async def _skills(self) -> list[Skill]:
        listed = await self._worker.ask(ListSkillsJob(publisher=self._worker.worker_name))
        if listed is None or not listed.skills:
            return []
        return list(listed.skills)

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
                await asyncio.sleep(1)
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
