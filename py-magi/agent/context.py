"""Conversation-context loading, compaction, and prompt rendering."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from bus import (
    ArchiveMessagesJob,
    CallLLMResult,
    ContactNote,
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


def messages_from_records(*, summary: str, records) -> list[LLMMessage]:
    """Render a durable conversation snapshot without reaching into BUS Books."""
    messages: list[LLMMessage] = []
    if summary:
        messages.append(
            LLMMessage(role=LLMMessageRole.USER, content=f"[Prior conversation summary]\n{summary}")
        )
    messages.extend(
        LLMMessage(
            role=LLMMessageRole.ASSISTANT if record.contact_id == 1 else LLMMessageRole.USER,
            content=record.content,
        )
        for record in records
    )
    return messages


def render_instruction_block(personal_instruction: str | None) -> str:
    value = (personal_instruction or "").strip()
    if not value:
        return ""
    return (
        "# Instructions\n"
        "These instructions are part of your operating context. Try to comply with all of them. "
        "If they conflict irreconcilably, explain the conflict instead of silently choosing one.\n\n"
        "## Your personal instruction\n" + value
    )


def format_system_prompt(
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
    parts = [soul]
    if instruction:
        parts.append(instruction)
    if skills:
        parts.append("## Available skills\n" + "\n".join(f"- {name}" for name in skills))
    if memories:
        parts.append(
            "## Long-term memory\n"
            + "\n".join(f"- {memory.topic}: {memory.detail}" for memory in memories)
        )
    if contact is not None:
        name = contact.nickname or contact.name or "Unknown"
        note_lines = [f"## Current chatter\nName: {name}"]
        note_lines.extend(f"- {note.note}" for note in notes if note.note)
        parts.append("\n".join(note_lines))
    if conversation_instruction:
        parts.append("## Conversation instruction\n" + conversation_instruction)
    if conversation_info:
        parts.append("## Conversation info\n" + conversation_info)
    if daily_note:
        parts.append("## Daily note\n" + daily_note)
    return "\n\n".join(part for part in parts if part).strip() or soul


class AgentContext:
    """Load and construct the data used by one conversation run."""

    def __init__(self, worker: AgentWorker, *, conversation_id: int) -> None:
        self._worker = worker
        self.conversation_id = conversation_id
        self.conversation = None
        self.history: list[LLMMessage] = []
        self.records: list = []
        self.system = ""
        self.tools = []

    async def get(self, contact_id: int) -> bool:
        """Refresh the context snapshot for the next run of this conversation."""
        conversation = await self._conversation()
        if conversation is None:
            return False
        history, records = await self._history(conversation.summary)
        self.conversation = conversation
        self.history = history
        self.records = records
        self.system = await self._system_prompt(
            contact_id,
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

    async def _history(self, summary: str) -> tuple[list[LLMMessage], list]:
        result = await self._worker.ask(
            ListConversationMessagesJob(
                publisher=self._worker.worker_name,
                conversation_id=self.conversation_id,
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
        soul = await self.prompt("agent/soul") or "You are a helpful assistant."
        instruction = render_instruction_block(await self.setting("instruction"))
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

    async def compact(
        self,
        *,
        summary: str,
        history: list[LLMMessage],
        records: list,
        call_llm: Callable[..., Awaitable[CallLLMResult | None]],
    ) -> list[LLMMessage]:
        window = await self.setting_int("compact_context_window", 100_000)
        threshold = window * await self.setting_int("compact_threshold_pct", 80) // 100
        if estimate_string_tokens(summary) + estimate_messages_tokens(history) <= threshold:
            return history
        keep = max(1, await self.setting_int("compact_keep_recent", 20))
        tail_records = records[-keep:]
        to_archive = records[: len(records) - len(tail_records)]
        if not to_archive:
            return history
        prompt = await self.prompt("agent/compaction")
        if not prompt:
            return history
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
            return history
        new_summary = result.message.content.strip()
        if not new_summary:
            return history
        updated = await self._worker.ask(
            UpdateConversationSummaryJob(
                publisher=self._worker.worker_name,
                conversation_id=self.conversation_id,
                summary=new_summary,
            )
        )
        if updated is None:
            return history
        await self._worker.ask(
            ArchiveMessagesJob(
                publisher=self._worker.worker_name,
                conversation_id=self.conversation_id,
                before_message_id=tail_records[0].id,
            )
        )
        return messages_from_records(summary=new_summary, records=tail_records)

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


__all__ = [
    "AgentContext",
    "format_system_prompt",
    "messages_from_records",
    "render_instruction_block",
]
