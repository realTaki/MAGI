"""Persistence-level delivery job board regression tests."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select

from magi.old_bus.bases.db import EngineFactory
from magi.old_bus.bases.db.base import utcnow_naive
from magi.old_bus.firmwares.schema import LOCAL_SCOPE, synchronise_schema
from magi.old_bus.firmwares.jobs.deliveryNotifyJob import (
    DeliveryNotifyJob,
    _DeliveryNotifyJobRow,
    deliveryNotifyJobBoard,
)
from magi.old_bus.firmwares.books.local.contactBook import Contact, ContactBook
from magi.old_bus.firmwares.books.local.conversationBook import (
    Conversation,
    ConversationBook,
    MessageBook,
)


@pytest.fixture
def board(tmp_path) -> deliveryNotifyJobBoard:
    factory = EngineFactory(f"sqlite:///{tmp_path / 'delivery.sqlite'}")
    synchronise_schema(factory, scope=LOCAL_SCOPE)
    return deliveryNotifyJobBoard(factory)


@pytest.fixture
def conversation_id(board: deliveryNotifyJobBoard) -> int:
    """Seed a contact + webui conversation so message writes can resolve."""
    cbook = ContactBook(board._factory)
    sbook = ConversationBook(board._factory)
    contact_id = cbook.get(cbook.add(Contact(name="delivery-test"))).id
    conv = sbook.get(sbook.add(Conversation(
        delivery_address="webui:1",
        contact_id=contact_id,
        channel="webui",
    )))
    return conv.id


def test_claim_for_channel_never_claims_another_channel(board: deliveryNotifyJobBoard) -> None:
    tg_job_id = board.publish(DeliveryNotifyJob(channel="tg", text="tg"))
    webui_job_id = board.publish(DeliveryNotifyJob(channel="webui", text="webui"))

    webui_claim = board.claim_for_channel(channel="webui", worker_id="webui-worker")
    assert webui_claim is not None
    assert webui_claim.job_id == webui_job_id
    assert webui_claim.channel == "webui"

    tg_claim = board.claim_for_channel(channel="tg", worker_id="tg-worker")
    assert tg_claim is not None
    assert tg_claim.job_id == tg_job_id
    assert tg_claim.channel == "tg"


def test_concurrent_channel_consumers_claim_a_job_once(board: deliveryNotifyJobBoard) -> None:
    board.publish(DeliveryNotifyJob(channel="webui", text="once"))
    other_consumer = deliveryNotifyJobBoard(board._factory)
    barrier = Barrier(2)

    def claim(candidate: deliveryNotifyJobBoard):
        barrier.wait()
        return candidate.claim_for_channel(channel="webui", worker_id=f"worker-{id(candidate)}")

    with ThreadPoolExecutor(max_workers=2) as executor:
        claims = list(executor.map(claim, (board, other_consumer)))

    assert sum(job is not None for job in claims) == 1


def test_channel_lease_recovery_never_auto_fails(
    board: deliveryNotifyJobBoard,
) -> None:
    job_id = board.publish(DeliveryNotifyJob(channel="webui", text="retry"))

    for worker_id in ("worker-a", "worker-b", "worker-c", "worker-d"):
        claim = board.claim_for_channel(channel="webui", worker_id=worker_id)
        assert claim is not None
        with board._session() as session:
            row = session.scalar(select(_DeliveryNotifyJobRow).where(_DeliveryNotifyJobRow.job_id == job_id))
            assert row is not None
            row.leased_until = utcnow_naive() - timedelta(seconds=1)
            session.commit()

    assert board.get_result(job_id=job_id) is None


# ---------------------------------------------------------------------------
# assistant-row chokepoint (mirrors chatNotifyBoard.publish)
# ---------------------------------------------------------------------------


def test_publish_writes_assistant_row_when_messages_book_wired(
    board: deliveryNotifyJobBoard,
    conversation_id: int,
) -> None:
    """Single chokepoint: publish writes the ``role='assistant'`` row,
    channel workers no longer reach into ``messages_book`` themselves.
    """
    mbook = MessageBook(board._factory)
    board_with = deliveryNotifyJobBoard(board._factory, messages_book=mbook)

    jid = board_with.publish(DeliveryNotifyJob(
        channel="webui",
        text="hello assistant",
        conversation_id=conversation_id,
        contact_id=1,
    ))
    assert jid > 0

    rows = mbook.list_for_conversation(conversation_id=conversation_id)
    assert len(rows) == 1
    assert rows[0].role == "assistant"
    assert rows[0].text == "hello assistant"


def test_publish_skips_message_write_when_messages_book_is_none(
    board: deliveryNotifyJobBoard,
    conversation_id: int,
) -> None:
    """Legacy / test-mode board (no messages_book) silently no-ops on DB."""
    jid = board.publish(DeliveryNotifyJob(
        channel="webui",
        text="no-book",
        conversation_id=conversation_id,
        contact_id=1,
    ))
    assert jid > 0
    # No row anywhere — messages_book was never wired.
    assert MessageBook(board._factory).list_for_conversation(conversation_id=conversation_id) == []


def test_publish_skips_message_write_when_conversation_id_is_none(
    board: deliveryNotifyJobBoard,
) -> None:
    """Orphan delivery (no conversation) — assistant row not even attempted."""
    mbook = MessageBook(board._factory)
    board_with = deliveryNotifyJobBoard(board._factory, messages_book=mbook)

    jid = board_with.publish(DeliveryNotifyJob(channel="tg", text="orphan"))
    assert jid > 0
    # No row, no crash — MessageBook.add(0) would have raised.
    assert mbook.list_for_conversation(conversation_id=0) == []


def test_publish_swallows_messages_book_failure(
    board: deliveryNotifyJobBoard,
    conversation_id: int,
) -> None:
    """A transient messages_book outage must NOT block the delivery job."""
    flaky = MagicMock()
    flaky.add.side_effect = RuntimeError("transient blip")
    board_flaky = deliveryNotifyJobBoard(board._factory, messages_book=flaky)

    jid = board_flaky.publish(DeliveryNotifyJob(
        channel="tg",
        text="will be enqueued",
        conversation_id=conversation_id,
        contact_id=1,
    ))
    assert jid > 0  # delivery job enqueued despite messages_book failure
    flaky.add.assert_called_once()


def test_publish_swallows_conversation_not_found(
    board: deliveryNotifyJobBoard,
) -> None:
    """Bad conversation_id → ``ConversationNotFoundError`` is swallowed,
    delivery job still enqueued (matches chatNotifyBoard behaviour).
    """
    mbook = MessageBook(board._factory)
    board_with = deliveryNotifyJobBoard(board._factory, messages_book=mbook)

    jid = board_with.publish(DeliveryNotifyJob(
        channel="tg",
        text="ghost",
        conversation_id=99999,
        contact_id=1,
    ))
    assert jid > 0
    assert mbook.list_for_conversation(conversation_id=99999) == []
