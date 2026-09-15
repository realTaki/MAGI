from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import channels.telegram.worker as telegram_worker
from bus import Bus, ChatNotify, DeliveryNotify, JobStatus, SetSettingJob
from channels.telegram.worker import TelegramWorker


@pytest.mark.asyncio
async def test_telegram_worker_attaches_without_a_bot_token(tmp_path) -> None:
    with Bus("@unit.magi", workspace=tmp_path / "workspace") as bus:
        assert bus.attach(TelegramWorker)
        worker = bus.workers["tg"]
        await asyncio.sleep(0)
        assert worker._listen is None


@pytest.mark.asyncio
async def test_telegram_worker_keeps_its_listener_running_after_attach(
    tmp_path, monkeypatch
) -> None:
    async def fake_listen(self, token: str, ready: asyncio.Event) -> None:
        del token
        ready.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(TelegramWorker, "_listen_bot", fake_listen)
    with Bus("@unit.magi", workspace=tmp_path / "workspace") as bus:
        assert bus.attach(TelegramWorker, settings={"bot_token": "token"})
        worker = bus.workers["tg"]
        await asyncio.sleep(0)
        assert worker._listen is not None
        assert not worker._listen.done()


@pytest.mark.asyncio
async def test_telegram_worker_bridges_one_chat_to_conversation_jobs(
    tmp_path, monkeypatch
) -> None:
    sent: list[tuple[str, int, str]] = []

    async def fake_send(token: str, chat_id: int, text: str) -> None:
        sent.append((token, chat_id, text))

    monkeypatch.setattr(telegram_worker, "send_text_raw", fake_send)
    with Bus("@unit.magi", workspace=tmp_path / "workspace") as bus:
        assert bus.attach(TelegramWorker)
        worker = bus.workers["tg"]
        await worker._on_tg_message(
            SimpleNamespace(
                effective_chat=SimpleNamespace(id=123456),
                effective_message=SimpleNamespace(text="hello"),
            ),
            None,
        )

        inbound = None
        for _ in range(20):
            inbound = bus.board(ChatNotify).claim()
            if inbound is not None:
                break
            await asyncio.sleep(0.05)
        assert inbound is not None
        assert inbound.text == "hello"
        assert inbound.channel == "tg"
        assert inbound.delivery_address == "123456"
        assert inbound.conversation_id > 0

        bus.board(SetSettingJob).publish(
            SetSettingJob(publisher="test", key="telegram.bot_token", value="fake-token")
        )
        delivery = bus.board(DeliveryNotify)
        delivery_id = delivery.publish(
            DeliveryNotify(
                publisher="test",
                conversation_id=inbound.conversation_id,
                text="reply",
            )
        )
        result = delivery.get_result(delivery_id, timeout=5.0)
        assert result is not None
        assert result.status is JobStatus.COMPLETED
        assert sent == [("fake-token", 123456, "reply")]
