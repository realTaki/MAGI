from __future__ import annotations

import asyncio

import pytest

import channels.asp.worker as asp_worker
from bus import Bus, ChatNotify, DeliveryNotify, GetConversationForChannelJob, JobStatus
from channels.asp.worker import AspWorker

_ASP_SETTINGS = {"handle": "@unit.magi", "base": "http://test", "token": "token"}


class FakeAspClient:
    def __init__(self, **_settings) -> None:
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
async def test_asp_worker_keeps_its_listener_running_after_attach(tmp_path, monkeypatch) -> None:
    client = FakeAspClient()
    monkeypatch.setattr(asp_worker, "AspClient", lambda **_kwargs: client)
    with Bus("@unit.magi", workspace=tmp_path / "workspace") as bus:
        assert bus.attach(AspWorker, settings=_ASP_SETTINGS)
        worker = bus.workers["asp"]
        await asyncio.sleep(0)
        assert worker._listen is not None
        assert not worker._listen.done()


@pytest.mark.asyncio
async def test_asp_worker_bridges_one_session_to_conversation_jobs(tmp_path, monkeypatch) -> None:
    client = FakeAspClient()
    monkeypatch.setattr(asp_worker, "AspClient", lambda **_kwargs: client)
    with Bus("@unit.magi", workspace=tmp_path / "workspace") as bus:
        assert bus.attach(AspWorker, settings=_ASP_SETTINGS)
        worker = bus.workers["asp"]
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

        inbound = None
        for _ in range(20):
            inbound = bus.board(ChatNotify).claim()
            if inbound is not None:
                break
            await asyncio.sleep(0.05)
        assert inbound is not None
        assert inbound.text == "hello"
        assert inbound.channel == "asp"
        assert inbound.delivery_address == "session-1"
        assert client.joined == ["session-1"]

        got = bus.board(GetConversationForChannelJob).publish(
            GetConversationForChannelJob(
                publisher="test",
                channel=inbound.channel,
                delivery_address=inbound.delivery_address,
            )
        )
        delivery = bus.board(DeliveryNotify)
        delivery_id = delivery.publish(
            DeliveryNotify(
                publisher="test",
                conversation_id=got.conversation.id,
                text="reply",
            )
        )
        result = delivery.get_result(delivery_id, timeout=5.0)
        assert result is not None
        assert result.status is JobStatus.COMPLETED
        assert client.sent == [("session-1", "reply")]
