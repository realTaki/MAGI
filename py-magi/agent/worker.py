"""Agent Worker: claim incoming turns and route them by conversation."""

from __future__ import annotations

from bus import BaseWorker, ChatNotify, RegisterPromptJob

from .conversations import Conversation
from .prompt_defaults import prompt_defaults


class AgentWorker(BaseWorker):
    """Route each claimed ``ChatNotify`` to its serial ``Conversation``."""

    worker_name = "agent"
    default_settings = {
        "max_tokens": "1024",
        "tool_wait_seconds": "300",
        "llm_timeout_seconds": "120",
        "compact_keep_recent": "20",
        "compact_context_window": "100000",
        "compact_threshold_pct": "80",
    }

    def __init__(self, bus, *, poll_seconds: float = 0.25) -> None:
        super().__init__(bus, poll_seconds=poll_seconds)
        self._conversations: dict[int, Conversation] = {}

    async def on_attached(self) -> None:
        for key, value in prompt_defaults():
            await self.ask(RegisterPromptJob(publisher=self.worker_name, key=key, value=value))

    async def _poll(self) -> bool:
        job = await self.claim(ChatNotify)
        if job is None:
            return False
        conversation = self._conversations.get(job.conversation_id)
        if conversation is None:
            conversation = Conversation(self, job.conversation_id)
            self._conversations[job.conversation_id] = conversation
        conversation.submit(job)
        return True

    def _release_conversation(self, conversation: Conversation) -> None:
        if self._conversations.get(conversation.conversation_id) is conversation:
            self._conversations.pop(conversation.conversation_id, None)
