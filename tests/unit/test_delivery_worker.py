"""Committed Telegram replies are sent by the durable outbox worker."""

from __future__ import annotations

import asyncio

import pytest

from magi.bus import AgentMessage, BusStore
from magi.bus.models import DeliveryOutbox
from magi.db import init_orm, open_session


@pytest.mark.asyncio
async def test_delivery_worker_sends_and_marks_reply_delivered(tmp_path, monkeypatch) -> None:
    state = tmp_path / "state"
    monkeypatch.setenv("MAGI_STATE_DIR", str(state))
    init_orm(str(state), seed_root=False)
    store = BusStore(str(state))
    run_id = store.publish_agent_message(
        AgentMessage(event_id="tg-event", text="hello", channel="tg")
    )
    claim = store.claim_next_agent_message("agent")
    assert claim is not None
    store.complete_agent_message(claim.event_id, "reply", delivery_destination="123")

    from magi.channels import delivery as delivery_mod
    from magi.channels.telegram import bot as bot_mod
    from magi.db import settings as settings_mod

    delivered: list[tuple[str, int, str]] = []

    async def fake_send(token: str, chat_id: int, text: str) -> None:
        delivered.append((token, chat_id, text))

    monkeypatch.setattr(bot_mod, "send_text_raw", fake_send)
    monkeypatch.setattr(settings_mod, "state_get", lambda *_args: "token")
    worker = delivery_mod.DeliveryWorker(str(state), poll_seconds=0.01)
    await worker.start()
    try:
        for _ in range(50):
            with open_session() as session:
                row = session.query(DeliveryOutbox).filter_by(run_id=run_id).one()
                if row.status == "delivered":
                    break
            await asyncio.sleep(0.01)
        else:
            pytest.fail("outbox delivery was not marked delivered")
    finally:
        await worker.stop()

    assert delivered == [("token", 123, "reply")]
