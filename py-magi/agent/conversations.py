"""Per-conversation serialization for independently claimed Agent turns."""

from __future__ import annotations

import asyncio
from collections import deque
from typing import TYPE_CHECKING

from bus import ChatNotify, go

if TYPE_CHECKING:
    from .worker import AgentWorker


class Conversation:
    """Own one conversation's serial turn queue and in-band steering buffer."""

    def __init__(self, worker: AgentWorker, conversation_id: int) -> None:
        self._worker = worker
        self.conversation_id = conversation_id
        self._pending: deque[ChatNotify] = deque()
        self._running = False

    def submit(self, job: ChatNotify) -> None:
        """Route a claimed Job to this conversation without starting a second turn."""
        self._pending.append(job)
        if self._running:
            return
        self._running = True
        go(self._run())

    def take_steering(self) -> list[ChatNotify]:
        """Take turns received while the active turn waits for tool results."""
        steering = list(self._pending)
        self._pending.clear()
        return steering

    async def _run(self) -> None:
        try:
            while self._pending:
                await self._worker._run_turn(self, self._pending.popleft())
        finally:
            self._running = False
            self._worker._release_conversation(self)
