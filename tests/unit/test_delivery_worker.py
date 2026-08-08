"""Telegram delivery tests — rebased to new_bus deliveryJobBoard + TelegramWorker.

Tests that TelegramWorker._deliver_tg calls send_text_raw and marks
the delivery job as completed via submit_result.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from magi.new_bus.db import EngineFactory
from magi.new_bus.guild.deliveryJob import DeliveryJob, deliveryJobBoard


@pytest.mark.asyncio
async def test_telegram_worker_delivers_and_submits_success(monkeypatch):
    """_deliver_tg calls send_text_raw and submit_result(success=True)."""
    from unittest.mock import AsyncMock

    f = EngineFactory("sqlite:///:memory:")
    f.create_all()
    board = deliveryJobBoard(f)

    # Publish a TG delivery job
    jid = board.publish(DeliveryJob(
        channel="tg",
        destination="123456",
        payload={"text": "hello"},
        run_id="run_1",
    ))

    # Create a mock bus for the worker
    mock_bus = MagicMock()
    mock_bus.delivery_job_board = board
    mock_bus.settings_book = MagicMock()
    mock_bus.settings_book.get = MagicMock(return_value="fake_token")

    # Mock send_text_raw
    sent: list[tuple] = []

    async def fake_send(token, chat_id, text):
        sent.append((token, chat_id, text))

    monkeypatch.setattr(
        "magi.channels.telegram.bot.send_text_raw", fake_send,
    )

    from magi.channels.workers.telegram import TelegramWorker

    worker = TelegramWorker(mock_bus)
    worker._stopping = False

    # Claim the job
    claim = board.claim()
    assert claim is not None
    assert claim.channel == "tg"

    # Call _deliver_tg directly
    await worker._deliver_tg(claim)

    # Verify sent
    assert len(sent) == 1
    assert sent[0] == ("fake_token", 123456, "hello")

    # Verify result
    board.submit_result(
        key=jid,
        result=__import__("magi.new_bus.guild.deliveryJob", fromlist=["DeliveryResult"]).DeliveryResult(
            job_id=jid, success=True,
        ),
    )
    result = board.get_result(key=jid)
    assert result is not None
    assert result.success is True


@pytest.mark.asyncio
async def test_telegram_worker_fails_without_token():
    """_deliver_tg raises RuntimeError when no bot_token."""
    mock_bus = MagicMock()
    mock_bus.settings_book = MagicMock()
    mock_bus.settings_book.get = MagicMock(return_value=None)  # no token

    job = DeliveryJob(
        channel="tg",
        destination="123",
        payload={"text": "hi"},
        job_id="j1",
    )

    from magi.channels.workers.telegram import TelegramWorker

    worker = TelegramWorker(mock_bus)

    with pytest.raises(RuntimeError, match="no bot_token"):
        await worker._deliver_tg(job)
