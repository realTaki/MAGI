from __future__ import annotations

import ast
from pathlib import Path

import pytest

from magi.new_bus import BookOp, Bus, InvalidJobError, JobStatus, ManageBookJob, Slot
from magi.new_bus.firmware import FIRMWARE_VERSION, ManageMessageJob, ManageMessageJobBoard, install
from magi.new_bus.testing import InMemoryBackend

MESSAGES = "messages"


@pytest.fixture
def bus() -> Bus:
    item = Bus(InMemoryBackend())
    install(item)
    return item


def write_message(bus: Bus, op: BookOp, **payload) -> ManageBookJob:
    job = bus.publish(ManageBookJob(book=MESSAGES, op=op, payload=payload))
    assert isinstance(job, ManageBookJob)
    return job


def create(bus: Bus, **payload) -> ManageBookJob:
    return write_message(bus, BookOp.CREATE, **payload)


def test_firmware_version_is_a_constant() -> None:
    assert FIRMWARE_VERSION == 1


def test_create_read_update_delete_message(bus: Bus) -> None:
    created = create(bus, role="user", content="hello", session_id="s1")
    assert created.status is JobStatus.COMPLETED
    record = created.result["record"]
    assert record["role"] == "user"
    assert record["content"] == "hello"
    assert record["session_id"] == "s1"

    by_id = write_message(bus, BookOp.READ, id=record["id"])
    assert by_id.status is JobStatus.COMPLETED
    assert by_id.result["record"]["content"] == "hello"

    listed = write_message(bus, BookOp.READ, filter={"session_id": "s1"})
    assert [item["id"] for item in listed.result["records"]] == [record["id"]]

    updated = write_message(bus, BookOp.UPDATE, id=record["id"], content="hello, world")
    assert updated.status is JobStatus.COMPLETED
    assert updated.result["record"]["content"] == "hello, world"
    assert updated.result["record"]["role"] == "user"

    deleted = write_message(bus, BookOp.DELETE, id=record["id"])
    assert deleted.status is JobStatus.COMPLETED
    missing = write_message(bus, BookOp.READ, id=record["id"])
    assert missing.status is JobStatus.FAILED


def test_invalid_message_fails_and_does_not_write(bus: Bus) -> None:
    assert create(bus, role="narrator", content="nope").status is JobStatus.FAILED
    assert create(bus, role="user", content="").status is JobStatus.FAILED
    listed = write_message(bus, BookOp.READ)
    assert listed.status is JobStatus.COMPLETED
    assert listed.result["records"] == []


def test_book_jobs_stay_on_the_book_board(bus: Bus) -> None:
    first = create(bus, role="assistant", content="ok")
    create(bus, role="nope", content="x")
    history = bus.list(ManageBookJob, book=MESSAGES)
    assert [job.status for job in history] == [JobStatus.COMPLETED, JobStatus.FAILED]
    assert isinstance(bus.get(ManageBookJob, first.id, book=MESSAGES), ManageBookJob)


def test_book_jobs_cannot_be_claimed(bus: Bus) -> None:
    create(bus, role="system", content="boot")
    with pytest.raises(InvalidJobError):
        bus.claim(ManageBookJob)


def test_message_job_board_is_on_the_bus(bus: Bus) -> None:
    board = bus.job_board(ManageMessageJob)
    assert isinstance(board, ManageMessageJobBoard)
    assert board is bus.job_board(ManageMessageJob)


def test_message_job_board_publish_claim_complete(bus: Bus) -> None:
    stored = create(bus, role="user", content="ping", session_id="s1")
    message_id = stored.result["record"]["id"]

    board = bus.job_board(ManageMessageJob)
    published = board.publish(
        ManageMessageJob(message_id=message_id, session_id="s1", publisher="inbox")
    )
    assert published.status is JobStatus.PENDING
    assert published.message_id == message_id

    claimed = board.claim()
    assert claimed is not None
    assert claimed.id == published.id
    assert claimed.status is JobStatus.CLAIMED
    assert claimed.message_id == message_id
    assert claimed.session_id == "s1"

    done = board.complete(claimed.id, result={"handled": True})
    assert done.status is JobStatus.COMPLETED
    assert board.get(done.id).result == {"handled": True}
    assert board.claim() is None


def test_message_job_board_keeps_history(bus: Bus) -> None:
    bus.publish(ManageMessageJob(message_id="m1"))
    first = bus.claim(ManageMessageJob)
    assert first is not None
    bus.complete(first)
    bus.publish(ManageMessageJob(message_id="m2"))
    second = bus.claim(ManageMessageJob)
    assert second is not None
    bus.fail(second, "nope")

    history = bus.list(ManageMessageJob)
    assert [job.message_id for job in history] == ["m1", "m2"]
    assert [job.status for job in history] == [JobStatus.COMPLETED, JobStatus.FAILED]


def test_publish_slot_on_book_job(bus: Bus) -> None:
    seen: list[str] = []
    bus.attach(ManageBookJob, Slot.PUBLISH, lambda job: seen.append(job.op.value))
    create(bus, role="tool", content="result")
    assert seen == ["create"]


def test_message_book_is_not_public() -> None:
    import magi.new_bus.firmware as firmware

    assert "MessageBook" not in firmware.__all__
    assert not hasattr(firmware, "MessageBook")
    assert "MessageBookJob" not in firmware.__all__


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
