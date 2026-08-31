from __future__ import annotations

import asyncio

import pytest

from bus import Bus, ChatNotify, DeliveryNotify, JobStatus
from channels.asp.worker import AspWorker


class FakeAspClient:
    def __init__(self) -> None:
        self.joined: list[str] = []
        self.sent: list[tuple[str, str]] = []

    async def listen(self, _on_event, *, ready: asyncio.Event) -> None:
        ready.set()
        await asyncio.Event().wait()

    async def join(self, session_id: str) -> None:
        self.joined.append(session_id)

    async def send(self, session_id: str, content: str) -> dict[str, str]:
        self.sent.append((session_id, content))
        return {}


@pytest.mark.asyncio
async def test_asp_worker_bridges_one_session_to_conversation_jobs(tmp_path) -> None:
    client = FakeAspClient()
    worker = AspWorker(
        handle="@unit.magi",
        base="http://127.0.0.1:9",
        token="token",
        client=client,  # type: ignore[arg-type]
    )
    with Bus(tmp_path / "workspace") as bus:
        worker.bus = bus
        await worker._on_event(
            {
                "type": "session.invited",
                "session_id": "session-1",
                "payload": {
                    "invitee": "@unit.magi",
                    "initial_message": {"content": "hello"},
                },
            }
        )

        inbound = bus.board(ChatNotify).claim()
        assert inbound is not None
        assert inbound.text == "hello"
        assert inbound.conversation_id is not None
        assert client.joined == ["session-1"]

        delivery = bus.board(DeliveryNotify)
        delivery_id = delivery.publish(
            DeliveryNotify(conversation_id=inbound.conversation_id, text="reply")
        )
        assert await worker._poll()
        result = delivery.get_result(delivery_id)
        assert result is not None
        assert result.status is JobStatus.COMPLETED
        assert client.sent == [("session-1", "reply")]
