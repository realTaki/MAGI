from __future__ import annotations

import dataclasses
from datetime import datetime

from magi.new_bus import (
    BaseRecord,
    BookOp,
    Bus,
    Conversation,
    JobStatus,
    OpenBookJob,
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
        op=op,
        record_id=int(record_id),
        filter=filt,
        values=values,
    )
    job.id = bus.publish(job, worker_id=WORKER, book=Conversation.__name__)
    return job


def result(bus: Bus, job: OpenBookJob):
    return bus.get_result(job, book=Conversation.__name__)


def create_conversation(bus: Bus, **values) -> OpenBookJob:
    values.setdefault("delivery_address", "webui:test")
    values.setdefault("contact_id", 1)
    values.setdefault("channel", "webui")
    return write_conversation(bus, BookOp.CREATE, **values)


def test_bus_starts_with_conversation_firmware() -> None:
    bus = _bus()
    assert Conversation.__name__ in bus.books
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
    assert result(bus, created).status is JobStatus.COMPLETED
    record = result(bus, created).record
    assert record["delivery_address"] == "tg:123"
    assert record["contact_id"] == 7
    assert record["channel"] == "tg"
    assert record["title"] == "hello"
    assert record["summary"] == "hi"
    assert record["last_compaction_at"] is None

    listed = write_conversation(bus, BookOp.READ, filter={"contact_id": 7})
    assert [item["id"] for item in result(bus, listed).records] == [record["id"]]


def test_last_compaction_at_round_trips() -> None:
    bus = _bus()
    stamped = datetime(2026, 8, 18, 12, 0)
    created = create_conversation(bus, title="compacted", last_compaction_at=stamped)
    assert result(bus, created).status is JobStatus.COMPLETED
    assert result(bus, created).record["last_compaction_at"] == stamped.isoformat()

    loaded = write_conversation(bus, BookOp.READ, id=result(bus, created).record["id"])
    assert result(bus, loaded).record["last_compaction_at"] == stamped.isoformat()


def test_messages_can_be_listed_by_conversation() -> None:
    from magi.new_bus import Message

    bus = _bus()
    conversation = create_conversation(bus, title="thread")
    conversation_id = result(bus, conversation).record["id"]

    bus.publish(
        OpenBookJob(
            op=BookOp.CREATE,
            values={"role": "user", "content": "hi", "conversation_id": conversation_id},
        ),
        worker_id=WORKER,
        book=Message.__name__,
    )
    listed = OpenBookJob(
        op=BookOp.READ,
        filter={"conversation_id": conversation_id},
    )
    listed.id = bus.publish(listed, worker_id=WORKER, book=Message.__name__)
    records = bus.get_result(listed, book=Message.__name__).records
    assert len(records) == 1
    assert records[0]["conversation_id"] == conversation_id
