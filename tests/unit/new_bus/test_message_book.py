from __future__ import annotations

import ast
import dataclasses
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pytest

from magi.new_bus import (
    BaseRecord,
    BookOp,
    Bus,
    Conversation,
    InvalidJobError,
    JobStatus,
    OpenBookJob,
    Message,
)
from magi.new_bus.base.openBookJob import OpenBookJobResult
from magi.new_bus.testing import WORKER, InMemoryBackend, occupy


@pytest.fixture
def bus() -> Bus:
    item = Bus(InMemoryBackend())
    occupy(item)
    return item


def write_message(
    bus: Bus,
    op: BookOp,
    record: Message | None = None,
    filter: dict | None = None,
) -> OpenBookJob[Message]:
    job = OpenBookJob[Message](op=op, record=record, filter=filter)
    job.id = bus.publish(job, worker_id=WORKER, book=Message.__name__)
    return job


def result(bus: Bus, job: OpenBookJob[Message]) -> OpenBookJobResult[Message]:
    outcome = bus.get_result(job, book=Message.__name__)
    assert outcome is not None
    return outcome


def create(bus: Bus, **values) -> OpenBookJob:
    return write_message(bus, BookOp.ADD, record=Message(**values))


def open_conversation(bus: Bus) -> int:
    job = OpenBookJob(
        op=BookOp.ADD,
        record=Conversation(delivery_address="webui:t", contact_id=1, channel="webui"),
    )
    job.id = bus.publish(job, worker_id=WORKER, book=Conversation.__name__)
    outcome = bus.get_result(job, book=Conversation.__name__)
    assert outcome is not None
    assert outcome.record is not None
    return int(outcome.record.id)


def test_bus_starts_with_firmware_books_and_jobs(bus: Bus) -> None:
    assert Message.__name__ in bus.books
    assert Conversation.__name__ in bus.books
    assert bus.record_type(Message.__name__) is Message
    assert bus.record_type(Conversation.__name__) is Conversation
    assert issubclass(Message, BaseRecord)
    assert {field.name for field in dataclasses.fields(BaseRecord)} == {
        "id",
        "created_at",
        "updated_at",
    }
    owned = {field.name for field in dataclasses.fields(BaseRecord)}
    assert {field.name for field in dataclasses.fields(Message)} - owned == {
        "role",
        "content",
        "conversation_id",
        "timestamp",
        "archived",
    }


def test_create_read_update_delete_message(bus: Bus) -> None:
    conversation_id = open_conversation(bus)
    created = create(bus, role="user", content="hello", conversation_id=conversation_id)
    assert result(bus, created).status is JobStatus.COMPLETED
    record = result(bus, created).record
    assert record is not None
    assert record.role == "user"
    assert record.content == "hello"
    assert record.conversation_id == conversation_id
    assert record.archived is False
    assert isinstance(record.timestamp, datetime)
    assert isinstance(record.created_at, datetime)
    assert isinstance(record.updated_at, datetime)

    by_id = write_message(bus, BookOp.GET, record=record)
    assert result(bus, by_id).status is JobStatus.COMPLETED
    got = result(bus, by_id).record
    assert got is not None
    assert got.content == "hello"

    listed = write_message(bus, BookOp.GET, filter={"conversation_id": conversation_id})
    records = result(bus, listed).records
    assert records is not None
    assert [item.id for item in records] == [record.id]

    updated = write_message(
        bus, BookOp.UPDATE, record=replace(record, content="hello, world")
    )
    assert result(bus, updated).status is JobStatus.COMPLETED
    updated_record = result(bus, updated).record
    assert updated_record is not None
    assert updated_record.content == "hello, world"
    assert updated_record.role == "user"

    deleted = write_message(bus, BookOp.DELETE, record=record)
    assert result(bus, deleted).status is JobStatus.COMPLETED
    missing = write_message(bus, BookOp.GET, record=record)
    assert result(bus, missing).status is JobStatus.FAILED


def test_timestamp_and_archived_round_trip(bus: Bus) -> None:
    stamped = datetime(2026, 8, 18, 9, 30)
    created = create(
        bus,
        role="assistant",
        content="later",
        conversation_id=open_conversation(bus),
        timestamp=stamped,
        archived=True,
    )
    assert result(bus, created).status is JobStatus.COMPLETED
    record = result(bus, created).record
    assert record is not None
    assert record.timestamp == stamped
    assert record.archived is True

    listed = write_message(bus, BookOp.GET, filter={"archived": True})
    records = result(bus, listed).records
    assert records is not None
    assert [item.id for item in records] == [record.id]


def test_missing_required_fields_fails_and_does_not_write(bus: Bus) -> None:
    assert result(bus, write_message(bus, BookOp.ADD)).status is JobStatus.FAILED
    listed = write_message(bus, BookOp.GET)
    assert result(bus, listed).status is JobStatus.COMPLETED
    assert result(bus, listed).records == []


def test_book_jobs_stay_on_the_book_board(bus: Bus) -> None:
    first = create(bus, role="assistant", content="ok")
    write_message(bus, BookOp.ADD)
    history = bus.list(OpenBookJob, book=Message.__name__)
    assert [result(bus, job).status for job in history] == [JobStatus.COMPLETED, JobStatus.FAILED]
    assert isinstance(
        next(job for job in history if job.id == first.id), OpenBookJob
    )


def test_book_jobs_cannot_be_claimed(bus: Bus) -> None:
    create(bus, role="system", content="boot")
    with pytest.raises(InvalidJobError):
        bus.claim(OpenBookJob, worker_id=WORKER)


def test_message_book_is_not_public() -> None:
    import magi.new_bus.firmware as firmware

    assert "MessageBook" not in firmware.__all__
    assert not hasattr(firmware, "MessageBook")
    assert "install" not in firmware.__all__
    assert "Message" in firmware.__all__
    assert "ConversationBook" not in firmware.__all__
    assert "Conversation" in firmware.__all__


def test_base_does_not_import_firmware() -> None:
    root = Path(__file__).resolve().parents[3] / "magi" / "new_bus" / "base"
    offenders: list[str] = []
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            if any("firmware" in name.split(".") for name in names):
                offenders.append(str(path))
    assert not offenders
