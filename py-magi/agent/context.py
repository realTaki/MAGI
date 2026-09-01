"""Conversation-context loading, compaction, and prompt rendering."""

from __future__ import annotations

from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import replace
from itertools import chain
from typing import TYPE_CHECKING

from bus import (
    ArchiveMessagesJob,
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
    NoteKind,
    UpdateConversationSummaryJob,
)

from .compaction import estimate_messages_tokens, estimate_string_tokens

if TYPE_CHECKING:
    from .worker import AgentWorker


class AgentContext:
    """Load and construct the data used by one conversation run."""

    def __init__(self, worker: AgentWorker, *, conversation_id: int) -> None:
        self._worker = worker
        self.conversation_id = conversation_id
        self.conversation = None
        self.contact_id = 0
        self.summary = ""
        self.history: list[LLMMessage] = []
        self.records: list = []
        self.system = ""
        self.tools = []
        self.jobs: list[ChatNotify] = []
        self.tool_rounds: deque[list[LLMMessage]] = deque()
        self.final_reply = ""
        self.failed = False
        self._assistant: LLMMessage | None = None

    def messages(self) -> list[LLMMessage]:
        """Return the next LLM input in its protocol order."""
        return list(
            chain(
                # 1. System context: soul, instructions, skills, memory, contact, conversation.
                (LLMMessage(role=LLMMessageRole.SYSTEM, content=self.system),),
                # 2. Durable summary of archived conversation history.
                self._summary_messages(),
                # 3. Non-archived conversation messages, in MessageBook order.
                self.history,
                # 4. The two newest complete tool rounds from this active run.
                *self.tool_rounds,
            )
        )

    def add_chat(self, job: ChatNotify) -> None:
        """Begin a run from one claimed ChatNotify."""
        self.jobs = [job]
        self.tool_rounds.clear()
        self.final_reply = ""
        self.failed = False
        self._assistant = None
        self.contact_id = job.contact_id

    async def load(self) -> bool:
        """Load this conversation's context snapshot for the current run."""
        conversation = await self._conversation()
        if conversation is None:
            return False
        history, records = await self._history()
        self.conversation = conversation
        self.summary = conversation.summary
        self.history = history
        self.records = records
        self.system = await self._system_prompt(
            self.contact_id,
            conversation.instruction,
            conversation.info,
        )
        self.tools = await self._tools()
        return True

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
        contact_id: int,
        conversation_instruction: str | None,
        conversation_info: str | None,
    ) -> str:
        soul = await self.prompt("agent/soul") or "You are a helpful assistant."
        instruction = self._render_instruction_block(await self.setting("instruction"))
        skills = await self.skills()
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
        return self._format_system_prompt(
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
    def _render_instruction_block(personal_instruction: str | None) -> str:
        value = (personal_instruction or "").strip()
        if not value:
            return ""
        return (
            "# Instructions\n"
            "These instructions are part of your operating context. Try to comply with all of them. "
            "If they conflict irreconcilably, explain the conflict instead of silently choosing one.\n\n"
            "## Your personal instruction\n" + value
        )

    @staticmethod
    def _format_system_prompt(
        *,
        soul: str,
        instruction: str,
        skills: list[str],
        memories,
        contact,
        notes,
        daily_note: str | None,
        conversation_instruction: str | None,
        conversation_info: str | None,
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
            name = contact.nickname or contact.name or "Unknown"
            contact_block = f"## Current chatter\nName: {name}"
            for note in notes:
                if note.note:
                    contact_block += f"\n- {note.note}"
            system += "\n\n" + contact_block
        if conversation_instruction:
            system += "\n\n## Conversation instruction\n" + conversation_instruction
        if conversation_info:
            system += "\n\n## Conversation info\n" + conversation_info
        if daily_note:
            system += "\n\n## Daily note\n" + daily_note
        return system.strip() or soul

    async def compact(
        self,
        *,
        call_llm: Callable[..., Awaitable[CallLLMResult | None]],
    ) -> None:
        conversation = self.conversation
        assert conversation is not None
        summary = self.summary
        history = self.history
        records = self.records
        window = await self.setting_int("compact_context_window", 100_000)
        threshold = window * await self.setting_int("compact_threshold_pct", 80) // 100
        if estimate_string_tokens(summary) + estimate_messages_tokens(history) <= threshold:
            return
        keep = max(1, await self.setting_int("compact_keep_recent", 20))
        tail_records = records[-keep:]
        to_archive = records[: len(records) - len(tail_records)]
        if not to_archive:
            return
        prompt = await self.prompt("agent/compaction")
        if not prompt:
            return
        source = "\n\n".join(
            f"[{message.role.value.upper()}]\n{message.content}"
            for message in history[: -len(tail_records)]
        )
        result = await call_llm(
            [
                LLMMessage(role=LLMMessageRole.SYSTEM, content=prompt),
                LLMMessage(role=LLMMessageRole.USER, content=source),
            ],
            [],
            max_tokens=1024,
        )
        if result is None or result.status is not JobStatus.COMPLETED or result.message is None:
            return
        new_summary = result.message.content.strip()
        if not new_summary:
            return
        updated = await self._worker.ask(
            UpdateConversationSummaryJob(
                publisher=self._worker.worker_name,
                conversation_id=self.conversation_id,
                summary=new_summary,
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
        conversation.summary = new_summary
        self.summary = new_summary
        self.records = tail_records
        self.history = self._messages_from_records(tail_records)

    def add_llm(self, message: LLMMessage) -> bool:
        """Accept one LLM result and report whether tool results are required."""
        self._assistant = message
        if message.tool_calls:
            return True
        self.final_reply = message.content
        return False

    def add_tools(self, messages: list[LLMMessage], pending: ChatNotify | None) -> None:
        """Add one complete tool round, retaining only its two newest instances."""
        assistant = self._assistant
        assert assistant is not None and assistant.tool_calls
        round_ = [assistant, *messages]
        self._assistant = None
        if pending is not None:
            self.jobs.append(pending)
            round_.append(LLMMessage(role=LLMMessageRole.USER, content=pending.text))
        self.tool_rounds.append(round_)
        while len(self.tool_rounds) > 2:
            for message in self.tool_rounds.popleft():
                if message.role is LLMMessageRole.TOOL:
                    continue
                if message.role is LLMMessageRole.ASSISTANT:
                    self.history.append(replace(message, tool_calls=None))
                else:
                    self.history.append(message)

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

    async def prompt(self, key: str) -> str | None:
        result = await self._worker.ask(GetPromptJob(publisher=self._worker.worker_name, key=key))
        return None if result is None else result.value

    async def skills(self) -> list[str]:
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
