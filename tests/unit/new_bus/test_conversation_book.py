from __future__ import annotations

import dataclasses
from datetime import datetime

from magi.new_bus import (
    BaseJobResult,
    BaseRecord,
    BookOp,
    Bus,
    Conversation,
    JobStatus,
    OpenBookJob,
    ManageConversationJob,
    ManageConversationJobBoard,
)
from magi.new_bus.testing import WORKER, InMemoryBackend, occupy


def _bus() -> Bus:
    item = Bus(InMemoryBackend())
    occupy(item)
    return item


def write_conversation(bus: Bus, op: BookOp, **values) -> OpenBookJob:
    record_id = values.pop("id", 0) or values.pop("record_id", 0) or 0
    filt = values.pop("filter", None)
    job = OpenBookJob(
        book=Conversation.__name__,
        op=op,
        record_id=int(record_id),
        filter=filt,
        values=values,
    )
    job.id = bus.publish(job, worker_id=WORKER)
    return job


def create_conversation(bus: Bus, **values) -> OpenBookJob:
    values.setdefault("delivery_address", "webui:test")
    values.setdefault("contact_id", 1)
    values.setdefault("channel", "webui")
    return write_conversation(bus, BookOp.CREATE, **values)


def test_bus_starts_with_conversation_firmware() -> None:
    bus = _bus()
    assert Conversation.__name__ in bus.books
    assert ManageConversationJob in bus.jobs
    assert bus.record_type(Conversation.__name__) is Conversation
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
    bus = _bus()
    created = create_conversation(
        bus,
        delivery_address="tg:123",
        contact_id=7,
        channel="tg",
        title="hello",
        summary="hi",
    )
    assert bus.get_result(created).status is JobStatus.COMPLETED
    record = bus.get_result(created).record
    assert record["delivery_address"] == "tg:123"
    assert record["contact_id"] == 7
    assert record["channel"] == "tg"
    assert record["title"] == "hello"
    assert record["summary"] == "hi"
    assert record["last_compaction_at"] is None

    listed = write_conversation(bus, BookOp.READ, filter={"contact_id": 7})
    assert [item["id"] for item in bus.get_result(listed).records] == [record["id"]]


def test_last_compaction_at_round_trips() -> None:
    bus = _bus()
    stamped = datetime(2026, 8, 18, 12, 0)
    created = create_conversation(bus, title="compacted", last_compaction_at=stamped)
    assert bus.get_result(created).status is JobStatus.COMPLETED
    assert bus.get_result(created).record["last_compaction_at"] == stamped.isoformat()

    loaded = write_conversation(bus, BookOp.READ, id=bus.get_result(created).record["id"])
    assert bus.get_result(loaded).record["last_compaction_at"] == stamped.isoformat()


def test_messages_can_be_listed_by_conversation() -> None:
    from magi.new_bus import Message

    bus = _bus()
    conversation = create_conversation(bus, title="thread")
    conversation_id = bus.get_result(conversation).record["id"]

    bus.publish(
        OpenBookJob(
            book=Message.__name__,
            op=BookOp.CREATE,
            values={"role": "user", "content": "hi", "conversation_id": conversation_id},
        ),
        worker_id=WORKER,
    )
    listed = OpenBookJob(
        book=Message.__name__,
        op=BookOp.READ,
        filter={"conversation_id": conversation_id},
    )
    listed.id = bus.publish(listed, worker_id=WORKER)
    records = bus.get_result(listed).records
    assert len(records) == 1
    assert records[0]["conversation_id"] == conversation_id


def test_conversation_job_board_publish_claim_complete() -> None:
    bus = _bus()
    stored = create_conversation(bus, title="inbox")
    conversation_id = bus.get_result(stored).record["id"]

    board = bus.job_board(ManageConversationJob)
    assert isinstance(board, ManageConversationJobBoard)
    board.publish(ManageConversationJob(conversation_id=conversation_id), worker_id=WORKER)
    claimed = board.claim(worker_id=WORKER)
    assert claimed is not None
    assert claimed.conversation_id == conversation_id
    board.submit_result(claimed.id, BaseJobResult(id=claimed.id), worker_id=WORKER)
    assert board.get_result(claimed.id).status is JobStatus.COMPLETED
