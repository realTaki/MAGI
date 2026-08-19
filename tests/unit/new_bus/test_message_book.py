from __future__ import annotations

import ast
import dataclasses
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
    ManageBookJob,
    ManageConversationJob,
    ManageMessageJob,
    ManageMessageJobBoard,
    Message,
    Slot,
)
from magi.new_bus.firmware import FIRMWARE_VERSION, FirmwareVersion
from magi.new_bus.testing import InMemoryBackend


@pytest.fixture
def bus() -> Bus:
    return Bus(InMemoryBackend())


def write_message(bus: Bus, op: BookOp, **values) -> ManageBookJob:
    record_id = values.pop("id", 0) or values.pop("record_id", 0) or 0
    filt = values.pop("filter", None)
    job = bus.publish(
        ManageBookJob(
            book=Message.BOOK,
            op=op,
            record_id=int(record_id),
            filter=filt,
            values=values,
        )
    )
    assert isinstance(job, ManageBookJob)
    return job


def create(bus: Bus, **values) -> ManageBookJob:
    return write_message(bus, BookOp.CREATE, **values)


def open_conversation(bus: Bus) -> int:
    job = bus.publish(
        ManageBookJob(
            book=Conversation.BOOK,
            op=BookOp.CREATE,
            values={"delivery_address": "webui:t", "contact_id": 1, "channel": "webui"},
        )
    )
    return int(bus.result(job).record["id"])


def test_firmware_version_is_a_constant() -> None:
    assert FIRMWARE_VERSION == FirmwareVersion(0, 0, 1)
    assert str(FIRMWARE_VERSION) == "0.0.1"


def test_bus_starts_with_firmware_books_and_jobs(bus: Bus) -> None:
    assert Message.BOOK in bus.books
    assert Conversation.BOOK in bus.books
    assert ManageMessageJob in bus.jobs
    assert ManageConversationJob in bus.jobs
    assert bus.record_type(Message.BOOK) is Message
    assert bus.record_type(Conversation.BOOK) is Conversation
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
    assert bus.result(created).status is JobStatus.COMPLETED
    record = bus.result(created).record
    assert record["role"] == "user"
    assert record["content"] == "hello"
    assert record["conversation_id"] == conversation_id
    assert record["archived"] is False
    datetime.fromisoformat(record["timestamp"])
    datetime.fromisoformat(record["created_at"])
    datetime.fromisoformat(record["updated_at"])

    by_id = write_message(bus, BookOp.READ, id=record["id"])
    assert bus.result(by_id).status is JobStatus.COMPLETED
    assert bus.result(by_id).record["content"] == "hello"

    listed = write_message(bus, BookOp.READ, filter={"conversation_id": conversation_id})
    assert [item["id"] for item in bus.result(listed).records] == [record["id"]]

    updated = write_message(bus, BookOp.UPDATE, id=record["id"], content="hello, world")
    assert bus.result(updated).status is JobStatus.COMPLETED
    assert bus.result(updated).record["content"] == "hello, world"
    assert bus.result(updated).record["role"] == "user"

    deleted = write_message(bus, BookOp.DELETE, id=record["id"])
    assert bus.result(deleted).status is JobStatus.COMPLETED
    missing = write_message(bus, BookOp.READ, id=record["id"])
    assert bus.result(missing).status is JobStatus.FAILED


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
    assert bus.result(created).status is JobStatus.COMPLETED
    record = bus.result(created).record
    assert record["timestamp"] == stamped.isoformat()
    assert record["archived"] is True

    listed = write_message(bus, BookOp.READ, filter={"archived": True})
    assert [item["id"] for item in bus.result(listed).records] == [record["id"]]


def test_missing_required_fields_fails_and_does_not_write(bus: Bus) -> None:
    assert bus.result(create(bus, content="nope")).status is JobStatus.FAILED
    listed = write_message(bus, BookOp.READ)
    assert bus.result(listed).status is JobStatus.COMPLETED
    assert bus.result(listed).records == []


def test_book_jobs_stay_on_the_book_board(bus: Bus) -> None:
    first = create(bus, role="assistant", content="ok")
    create(bus, content="x")
    history = bus.list(ManageBookJob, book=Message.BOOK)
    assert [bus.result(job).status for job in history] == [JobStatus.COMPLETED, JobStatus.FAILED]
    assert isinstance(bus.get(ManageBookJob, first.id, book=Message.BOOK), ManageBookJob)


def test_book_jobs_cannot_be_claimed(bus: Bus) -> None:
    create(bus, role="system", content="boot")
    with pytest.raises(InvalidJobError):
        bus.claim(ManageBookJob)


def test_message_job_board_is_on_the_bus(bus: Bus) -> None:
    board = bus.job_board(ManageMessageJob)
    assert isinstance(board, ManageMessageJobBoard)
    assert board is bus.job_board(ManageMessageJob)


def test_message_job_board_publish_claim_complete(bus: Bus) -> None:
    stored = create(bus, role="user", content="ping", conversation_id=open_conversation(bus))
    message_id = bus.result(stored).record["id"]

    board = bus.job_board(ManageMessageJob)
    published = board.publish(
        ManageMessageJob(message_id=message_id, conversation_id=1, publisher="inbox")
    )
    assert board.result(published.id).status is JobStatus.PENDING
    assert published.message_id == message_id

    claimed = board.claim()
    assert claimed is not None
    assert claimed.id == published.id
    assert board.result(claimed.id).status is JobStatus.CLAIMED
    assert claimed.message_id == message_id
    assert claimed.conversation_id == 1

    done = board.complete(claimed.id)
    assert board.result(done.id).status is JobStatus.COMPLETED
    loaded = board.get(done.id)
    outcome = board.result(loaded.id)
    assert outcome.status is JobStatus.COMPLETED
    assert outcome.id == done.id
    assert board.claim() is None


def test_message_job_board_keeps_history(bus: Bus) -> None:
    bus.publish(ManageMessageJob(message_id=1))
    first = bus.claim(ManageMessageJob)
    assert first is not None
    bus.complete(first)
    bus.publish(ManageMessageJob(message_id=2))
    second = bus.claim(ManageMessageJob)
    assert second is not None
    bus.fail(second, "nope")

    history = bus.list(ManageMessageJob)
    assert [job.message_id for job in history] == [1, 2]
    assert [bus.result(job).status for job in history] == [JobStatus.COMPLETED, JobStatus.FAILED]


def test_publish_slot_on_book_job(bus: Bus) -> None:
    seen: list[str] = []
    bus.attach(ManageBookJob, Slot.PUBLISH, lambda job: seen.append(job.op.value))
    create(bus, role="tool", content="result")
    assert seen == ["create"]


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
