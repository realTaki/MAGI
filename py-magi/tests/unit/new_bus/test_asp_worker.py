from __future__ import annotations

import asyncio

import pytest

import channels.asp.worker as asp_worker
from bus import Bus, ChatNotify, DeliveryNotify, JobStatus
from channels.asp.worker import AspWorker


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
        assert bus.boost_default_settings(worker_name="asp", settings={"handle": "@unit.magi", "base": "http://test", "token": "token"})
        worker = AspWorker(bus)
        await worker.on_attached()
        await asyncio.sleep(0)
        assert worker._listen_task is not None
        assert not worker._listen_task.done()
        await worker.on_detached()


@pytest.mark.asyncio
async def test_asp_worker_bridges_one_session_to_conversation_jobs(tmp_path, monkeypatch) -> None:
    client = FakeAspClient()
    monkeypatch.setattr(asp_worker, "AspClient", lambda **_kwargs: client)
    with Bus("@unit.magi", workspace=tmp_path / "workspace") as bus:
        assert bus.boost_default_settings(worker_name="asp", settings={"handle": "@unit.magi", "base": "http://test", "token": "token"})
        worker = AspWorker(bus)
        await worker.on_attached()
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
        assert inbound.conversation_id is not None
        assert client.joined == ["session-1"]

        delivery = bus.board(DeliveryNotify)
        delivery_id = delivery.publish(
            DeliveryNotify(conversation_id=inbound.conversation_id, text="reply")
        )
        for _ in range(20):
            if await worker._poll():
                break
            await asyncio.sleep(0.05)
        else:
            pytest.fail("ASP worker did not claim the delivery job")
        result = delivery.get_result(delivery_id)
        assert result is not None
        assert result.status is JobStatus.COMPLETED
        assert client.sent == [("session-1", "reply")]
