"""One conversation run's durable snapshot and transient LLM context."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from itertools import chain
from typing import TYPE_CHECKING, Any

from bus import (
    ArchiveMessagesJob,
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
    NoteKind,
    UpdateConversationSummaryJob,
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
class AgentContext:
    """All state and context construction for one serial ``Conversation`` run."""

    _worker: AgentWorker
    conversation_id: int

    conversation: Any | None = field(init=False, default=None)
    contact_id: int = field(init=False, default=0)

    # LLM message layout: SYSTEM → summary → history → retained text → tool rounds.
    system: str = field(init=False, default="")
    summary: str = field(init=False, default="")
    history: list[LLMMessage] = field(init=False, default_factory=list)
    records: list[Any] = field(init=False, default_factory=list)
    retained_text: list[LLMMessage] = field(init=False, default_factory=list)
    tool_rounds: tuple[ToolRound, ...] = field(init=False, default=())

    tools: list[Any] = field(init=False, default_factory=list)
    jobs: list[ChatNotify] = field(init=False, default_factory=list)
    final_reply: str = field(init=False, default="")
    failed: bool = field(init=False, default=False)
    _assistant: LLMMessage | None = field(init=False, default=None)

    def messages(self) -> list[LLMMessage]:
        """Return the next LLM input in its protocol order."""
        return list(
            chain(
                # 1. System context: soul, instructions, skills, memory, contact, conversation.
                (LLMMessage(role=LLMMessageRole.SYSTEM, content=self.system),),
                # 2. Durable summary of archived conversation history.
                self._summary_messages(),
                # 3. Non-archived messages in MessageBook order.
                self.history,
                # 4. Text retained from earlier tool rounds in this active run.
                self.retained_text,
                # 5. The two newest complete tool rounds from this active run.
                tool_rounds_messages(self.tool_rounds),
            )
        )

    def add_chat(self, job: ChatNotify) -> None:
        """Begin a run from one claimed ChatNotify."""
        self.jobs = [job]
        self.contact_id = job.contact_id
        self.tool_rounds = ()
        self.retained_text = []
        self.final_reply = ""
        self.failed = False
        self._assistant = None

    async def refresh(self) -> bool:
        """Refresh the per-turn view from durable storage."""
        conversation = await self._conversation()
        if conversation is None:
            return False
        self.conversation = conversation
        self.summary = conversation.summary
        self.history, self.records = await self._history()
        self.system = await self._system_prompt(
            conversation_instruction=conversation.instruction,
            conversation_info=conversation.info,
        )
        self.tools = await self._tools()
        return True

    async def compact(
        self,
        *,
        call_llm: Callable[..., Awaitable[CallLLMResult | None]],
        max_tokens: int,
    ) -> None:
        """Summarize durable history when the next complete payload exceeds budget."""
        threshold = await self._context_threshold()
        if self._estimated_payload_tokens(max_tokens=max_tokens) <= threshold:
            return

        keep = max(1, await self.setting_int("compact_keep_recent", 20))
        tail_records = self.records[-keep:]
        if len(tail_records) == len(self.records):
            return
        source_messages = [
            *self._summary_messages(),
            *self.history[: len(self.history) - len(tail_records)],
        ]
        prompt = await self.prompt("agent/compaction")
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

    def add_llm(self, message: LLMMessage) -> bool:
        """Add an LLM result and report whether this run must execute tools."""
        self._assistant = message
        if message.tool_calls:
            return True
        self.final_reply = message.content
        return False

    def add_tools(self, results: list[LLMMessage], pending: ChatNotify | None) -> None:
        """Add one complete tool round, preserving only the two newest exchanges."""
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

    def fail(self, error: str) -> None:
        self.failed = True
        self.final_reply = error

    def deliver(self) -> None:
        self._worker.publish_notify(
            DeliveryNotify(
                publisher=self._worker.worker_name,
                conversation_id=self.conversation_id,
                text=self.final_reply or "处理完毕。",
            )
        )

    def settle(self) -> None:
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
        soul = await self.prompt("agent/soul") or "You are a helpful assistant."
        instruction = self._instruction_block(await self.setting("instruction"))
        skills = await self.skills()
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

    async def _tools(self):
        result = await self._worker.ask(ListToolsJob(publisher=self._worker.worker_name))
        return (
            []
            if result is None or result.tools is None
            else [tool.definition for tool in result.tools]
        )

    async def _context_threshold(self) -> int:
        window = await self.setting_int("compact_context_window", 100_000)
        percent = await self.setting_int("compact_threshold_pct", 80)
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

    async def prompt(self, key: str) -> str | None:
        result = await self._worker.ask(GetPromptJob(publisher=self._worker.worker_name, key=key))
        return None if result is None else result.value

    async def skills(self) -> list[str]:
        listed = await self._worker.ask(ListSkillsJob(publisher=self._worker.worker_name))
        if listed is None or not listed.names:
            return []
        return [
            name
            for name in listed.names
            if await self._worker.ask(GetSkillJob(publisher=self._worker.worker_name, name=name))
            is not None
        ]

    async def setting(self, key: str) -> str | None:
        result = await self._worker.ask(GetSettingJob(publisher=self._worker.worker_name, key=key))
        if result is not None and result.value is not None:
            return result.value
        result = await self._worker.ask(
            GetSettingJob(publisher=self._worker.worker_name, key=f"agent.{key}")
        )
        return None if result is None else result.value

    async def setting_int(self, key: str, default: int) -> int:
        try:
            return int(await self.setting(key) or default)
        except ValueError:
            return default

    async def setting_float(self, key: str, default: float) -> float:
        try:
            return float(await self.setting(key) or default)
        except ValueError:
            return default


__all__ = ["AgentContext"]
