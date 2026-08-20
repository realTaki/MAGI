from __future__ import annotations

import ast
import dataclasses
from datetime import datetime
from pathlib import Path

import pytest

from magi.new_bus import (
    BaseJobResult,
    BaseRecord,
    BookOp,
    Bus,
    Conversation,
    InvalidJobError,
    JobStatus,
    OpenBookJob,
    ManageConversationJob,
    ManageMessageJob,
    ManageMessageJobBoard,
    Message,
)
from magi.new_bus.testing import WORKER, InMemoryBackend, occupy


@pytest.fixture
def bus() -> Bus:
    item = Bus(InMemoryBackend())
    occupy(item)
    return item


def write_message(bus: Bus, op: BookOp, **values) -> OpenBookJob:
    record_id = values.pop("id", 0) or values.pop("record_id", 0) or 0
    filt = values.pop("filter", None)
    job = OpenBookJob(
        op=op,
        record_id=int(record_id),
        filter=filt,
        values=values,
    )
    job.id = bus.publish(job, worker_id=WORKER, book=Message.__name__)
    return job


def result(bus: Bus, job: OpenBookJob):
    return bus.get_result(job, book=Message.__name__)


def create(bus: Bus, **values) -> OpenBookJob:
    return write_message(bus, BookOp.CREATE, **values)


def open_conversation(bus: Bus) -> int:
    job = OpenBookJob(
        op=BookOp.CREATE,
        values={"delivery_address": "webui:t", "contact_id": 1, "channel": "webui"},
    )
    job.id = bus.publish(job, worker_id=WORKER, book=Conversation.__name__)
    return int(bus.get_result(job, book=Conversation.__name__).record["id"])


def test_bus_starts_with_firmware_books_and_jobs(bus: Bus) -> None:
    assert Message.__name__ in bus.books
    assert Conversation.__name__ in bus.books
    assert ManageMessageJob in bus.jobs
    assert ManageConversationJob in bus.jobs
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
    assert record["role"] == "user"
    assert record["content"] == "hello"
    assert record["conversation_id"] == conversation_id
    assert record["archived"] is False
    datetime.fromisoformat(record["timestamp"])
    datetime.fromisoformat(record["created_at"])
    datetime.fromisoformat(record["updated_at"])

    by_id = write_message(bus, BookOp.READ, id=record["id"])
    assert result(bus, by_id).status is JobStatus.COMPLETED
    assert result(bus, by_id).record["content"] == "hello"

    listed = write_message(bus, BookOp.READ, filter={"conversation_id": conversation_id})
    assert [item["id"] for item in result(bus, listed).records] == [record["id"]]

    updated = write_message(bus, BookOp.UPDATE, id=record["id"], content="hello, world")
    assert result(bus, updated).status is JobStatus.COMPLETED
    assert result(bus, updated).record["content"] == "hello, world"
    assert result(bus, updated).record["role"] == "user"

    deleted = write_message(bus, BookOp.DELETE, id=record["id"])
    assert result(bus, deleted).status is JobStatus.COMPLETED
    missing = write_message(bus, BookOp.READ, id=record["id"])
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
    assert record["timestamp"] == stamped.isoformat()
    assert record["archived"] is True

    listed = write_message(bus, BookOp.READ, filter={"archived": True})
    assert [item["id"] for item in result(bus, listed).records] == [record["id"]]


def test_missing_required_fields_fails_and_does_not_write(bus: Bus) -> None:
    assert result(bus, create(bus, content="nope")).status is JobStatus.FAILED
    listed = write_message(bus, BookOp.READ)
    assert result(bus, listed).status is JobStatus.COMPLETED
    assert result(bus, listed).records == []


def test_book_jobs_stay_on_the_book_board(bus: Bus) -> None:
    first = create(bus, role="assistant", content="ok")
    create(bus, content="x")
    history = bus.list(OpenBookJob, book=Message.__name__)
    assert [result(bus, job).status for job in history] == [JobStatus.COMPLETED, JobStatus.FAILED]
    assert isinstance(
        next(job for job in history if job.id == first.id), OpenBookJob
    )


def test_book_jobs_cannot_be_claimed(bus: Bus) -> None:
    create(bus, role="system", content="boot")
    with pytest.raises(InvalidJobError):
        bus.claim(OpenBookJob, worker_id=WORKER)


def test_message_job_board_is_on_the_bus(bus: Bus) -> None:
    board = bus.job_board(ManageMessageJob)
    assert isinstance(board, ManageMessageJobBoard)
    assert board is bus.job_board(ManageMessageJob)


def test_message_job_board_publish_claim_complete(bus: Bus) -> None:
    stored = create(bus, role="user", content="ping", conversation_id=open_conversation(bus))
    message_id = result(bus, stored).record["id"]

    board = bus.job_board(ManageMessageJob)
    published = ManageMessageJob(message_id=message_id, conversation_id=1, publisher="inbox")
    job_id = board.publish(published, worker_id=WORKER)
    assert board.get_result(job_id) is None
    assert board.check_job_status(job_id) is JobStatus.PENDING
    assert published.message_id == message_id

    claimed = board.claim(worker_id=WORKER)
    assert claimed is not None
    assert claimed.id == job_id
    assert board.get_result(claimed.id) is None
    assert board.check_job_status(claimed.id) is JobStatus.CLAIMED
    assert claimed.message_id == message_id
    assert claimed.conversation_id == 1

    board.submit_result(claimed.id, BaseJobResult(id=claimed.id), worker_id=WORKER)
    assert board.get_result(claimed.id).status is JobStatus.COMPLETED
    outcome = board.get_result(claimed.id)
    assert outcome.status is JobStatus.COMPLETED
    assert outcome.id == claimed.id
    assert board.claim(worker_id=WORKER) is None


def test_message_job_board_keeps_history(bus: Bus) -> None:
    bus.publish(ManageMessageJob(message_id=1), worker_id=WORKER)
    first = bus.claim(ManageMessageJob, worker_id=WORKER)
    assert first is not None
    bus.submit_result(first, BaseJobResult(id=first.id), worker_id=WORKER)
    bus.publish(ManageMessageJob(message_id=2), worker_id=WORKER)
    second = bus.claim(ManageMessageJob, worker_id=WORKER)
    assert second is not None
    bus.submit_result(second, BaseJobResult(status=JobStatus.FAILED, error="nope"), worker_id=WORKER)

    history = bus.list(ManageMessageJob)
    assert [job.message_id for job in history] == [1, 2]
    assert [bus.get_result(job).status for job in history] == [JobStatus.COMPLETED, JobStatus.FAILED]


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
