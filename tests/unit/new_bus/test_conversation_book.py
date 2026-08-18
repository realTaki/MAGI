from __future__ import annotations

import dataclasses
from datetime import UTC, datetime

from magi.new_bus import (
    BaseRecord,
    BookOp,
    Bus,
    Conversation,
    JobStatus,
    ManageBookJob,
    ManageConversationJob,
    ManageConversationJobBoard,
)
from magi.new_bus.testing import InMemoryBackend


def write_conversation(bus: Bus, op: BookOp, **payload) -> ManageBookJob:
    job = bus.publish(ManageBookJob(book=Conversation.BOOK, op=op, payload=payload))
    assert isinstance(job, ManageBookJob)
    return job


def create_conversation(bus: Bus, **payload) -> ManageBookJob:
    payload.setdefault("delivery_address", "webui:test")
    payload.setdefault("contact_id", 1)
    payload.setdefault("channel", "webui")
    return write_conversation(bus, BookOp.CREATE, **payload)


def test_bus_starts_with_conversation_firmware() -> None:
    bus = Bus(InMemoryBackend())
    assert Conversation.BOOK in bus.books
    assert ManageConversationJob in bus.jobs
    assert bus.record_type(Conversation.BOOK) is Conversation
    owned = {field.name for field in dataclasses.fields(BaseRecord)}
    assert {field.name for field in dataclasses.fields(Conversation)} - owned == {
        "delivery_address",
        "contact_id",
        "channel",
        "title",
        "summary",
        "last_compaction_at",
    }


def test_create_read_filter_conversation() -> None:
    bus = Bus(InMemoryBackend())
    created = create_conversation(
        bus,
        delivery_address="tg:123",
        contact_id=7,
        channel="tg",
        title="hello",
        summary="hi",
    )
    assert created.status is JobStatus.COMPLETED
    record = bus.result(created).record
    assert record["delivery_address"] == "tg:123"
    assert record["contact_id"] == 7
    assert record["channel"] == "tg"
    assert record["title"] == "hello"
    assert record["summary"] == "hi"
    assert record["last_compaction_at"] is None

    listed = write_conversation(bus, BookOp.READ, filter={"contact_id": 7})
    assert [item["id"] for item in bus.result(listed).records] == [record["id"]]


def test_last_compaction_at_round_trips() -> None:
    bus = Bus(InMemoryBackend())
    stamped = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)
    created = create_conversation(bus, title="compacted", last_compaction_at=stamped)
    assert created.status is JobStatus.COMPLETED
    assert bus.result(created).record["last_compaction_at"] == stamped.isoformat()

    loaded = write_conversation(bus, BookOp.READ, id=bus.result(created).record["id"])
    assert bus.result(loaded).record["last_compaction_at"] == stamped.isoformat()


def test_messages_can_be_listed_by_conversation() -> None:
    from magi.new_bus import Message

    bus = Bus(InMemoryBackend())
    conversation = create_conversation(bus, title="thread")
    conversation_id = bus.result(conversation).record["id"]

    bus.publish(
        ManageBookJob(
            book=Message.BOOK,
            op=BookOp.CREATE,
            payload={"role": "user", "content": "hi", "conversation_id": conversation_id},
        )
    )
    listed = bus.publish(
        ManageBookJob(
            book=Message.BOOK,
            op=BookOp.READ,
            payload={"filter": {"conversation_id": conversation_id}},
        )
    )
    records = bus.result(listed).records
    assert len(records) == 1
    assert records[0]["conversation_id"] == conversation_id


def test_conversation_job_board_publish_claim_complete() -> None:
    bus = Bus(InMemoryBackend())
    stored = create_conversation(bus, title="inbox")
    conversation_id = bus.result(stored).record["id"]

    board = bus.job_board(ManageConversationJob)
    assert isinstance(board, ManageConversationJobBoard)
    board.publish(ManageConversationJob(conversation_id=conversation_id))
    claimed = board.claim()
    assert claimed is not None
    assert claimed.conversation_id == conversation_id
    done = board.complete(claimed.id)
    assert done.status is JobStatus.COMPLETED
