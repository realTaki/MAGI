from __future__ import annotations

import dataclasses

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


def test_bus_starts_with_conversation_firmware() -> None:
    bus = Bus(InMemoryBackend())
    assert Conversation.BOOK in bus.books
    assert ManageConversationJob in bus.jobs
    assert bus.record_type(Conversation.BOOK) is Conversation
    owned = {field.name for field in dataclasses.fields(BaseRecord)}
    assert {field.name for field in dataclasses.fields(Conversation)} - owned == {"title"}


def test_create_read_filter_conversation() -> None:
    bus = Bus(InMemoryBackend())
    created = write_conversation(bus, BookOp.CREATE, title="hello")
    assert created.status is JobStatus.COMPLETED
    assert created.result is not None
    record = created.result.record
    assert record["title"] == "hello"

    listed = write_conversation(bus, BookOp.READ, filter={"title": "hello"})
    assert listed.result is not None
    assert [item["id"] for item in listed.result.records] == [record["id"]]


def test_messages_can_be_listed_by_conversation() -> None:
    from magi.new_bus import Message

    bus = Bus(InMemoryBackend())
    conversation = write_conversation(bus, BookOp.CREATE, title="thread")
    assert conversation.result is not None
    conversation_id = conversation.result.record["id"]

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
    assert listed.result is not None
    assert len(listed.result.records) == 1
    assert listed.result.records[0]["conversation_id"] == conversation_id


def test_conversation_job_board_publish_claim_complete() -> None:
    bus = Bus(InMemoryBackend())
    stored = write_conversation(bus, BookOp.CREATE, title="inbox")
    assert stored.result is not None
    conversation_id = stored.result.record["id"]

    board = bus.job_board(ManageConversationJob)
    assert isinstance(board, ManageConversationJobBoard)
    board.publish(ManageConversationJob(conversation_id=conversation_id))
    claimed = board.claim()
    assert claimed is not None
    assert claimed.conversation_id == conversation_id
    done = board.complete(claimed.id)
    assert done.status is JobStatus.COMPLETED
