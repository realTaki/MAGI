"""Best-effort in-process streaming for an actor LLM attempt.

The hub intentionally has no durable queue.  SQLite remains the authority:
clients use these events for responsive drafts and reload committed history
when they reconnect.  Subscribers are bounded so a slow WebSocket/SSE client
can never stall an agent step.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, AsyncIterator


@dataclass(frozen=True, slots=True)
class StreamEvent:
    run_id: str
    attempt_id: str
    sequence_number: int
    kind: str
    payload: dict[str, Any]


class StreamHub:
    """A small, local pub/sub hub keyed by a durable run id."""

    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue[StreamEvent]]] = {}

    def subscribe(self, run_id: str) -> asyncio.Queue[StreamEvent]:
        queue: asyncio.Queue[StreamEvent] = asyncio.Queue(maxsize=128)
        self._subscribers.setdefault(run_id, set()).add(queue)
        return queue

    def unsubscribe(self, run_id: str, queue: asyncio.Queue[StreamEvent]) -> None:
        subscribers = self._subscribers.get(run_id)
        if not subscribers:
            return
        subscribers.discard(queue)
        if not subscribers:
            self._subscribers.pop(run_id, None)

    async def events(self, run_id: str) -> AsyncIterator[StreamEvent]:
        queue = self.subscribe(run_id)
        try:
            while True:
                yield await queue.get()
        finally:
            self.unsubscribe(run_id, queue)

    def publish(self, event: StreamEvent) -> None:
        for queue in tuple(self._subscribers.get(event.run_id, ())):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # Deltas are explicitly best-effort.  The committed result is
                # still available from SQLite and is emitted after transition
                # commit, so dropping a draft event is safe.
                continue


_hub = StreamHub()


def get_stream_hub() -> StreamHub:
    return _hub


__all__ = ["StreamEvent", "StreamHub", "get_stream_hub"]
