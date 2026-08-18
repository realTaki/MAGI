from __future__ import annotations

import pytest

from magi.new_bus import BookOp, Bus, InvalidJobError, JobStatus, ManageBookJob
from magi.new_bus.base.book import Book
from magi.new_bus.testing import PingJob, book_job


def test_create_read_update_delete(bus: Bus) -> None:
    created = bus.publish(book_job(BookOp.CREATE, name="alpha", kind="x"))
    assert created.status is JobStatus.COMPLETED
    record = created.result["record"]
    record_id = record["id"]
    assert record["name"] == "alpha"

    by_id = bus.publish(book_job(BookOp.READ, id=record_id))
    assert by_id.status is JobStatus.COMPLETED
    assert by_id.result["record"]["name"] == "alpha"

    listed = bus.publish(book_job(BookOp.READ, filter={"kind": "x"}))
    assert listed.status is JobStatus.COMPLETED
    assert [item["id"] for item in listed.result["records"]] == [record_id]

    updated = bus.publish(book_job(BookOp.UPDATE, id=record_id, name="beta"))
    assert updated.status is JobStatus.COMPLETED
    assert updated.result["record"]["name"] == "beta"

    deleted = bus.publish(book_job(BookOp.DELETE, id=record_id))
    assert deleted.status is JobStatus.COMPLETED
    missing = bus.publish(book_job(BookOp.READ, id=record_id))
    assert missing.status is JobStatus.FAILED
    assert missing.error


def test_failed_mutation_leaves_book_valid(bus: Bus) -> None:
    created = bus.publish(book_job(BookOp.CREATE, name="keep"))
    record_id = created.result["record"]["id"]

    failed = bus.publish(book_job(BookOp.UPDATE, name="no-id"))
    assert failed.status is JobStatus.FAILED

    still = bus.publish(book_job(BookOp.READ, id=record_id))
    assert still.status is JobStatus.COMPLETED
    assert still.result["record"]["name"] == "keep"

    missing = bus.publish(book_job(BookOp.DELETE, id="nope"))
    assert missing.status is JobStatus.FAILED
    still = bus.publish(book_job(BookOp.READ, id=record_id))
    assert still.status is JobStatus.COMPLETED


def test_book_jobs_are_not_claimed(bus: Bus) -> None:
    bus.publish(book_job(BookOp.CREATE, name="x"))
    with pytest.raises(InvalidJobError):
        bus.claim(ManageBookJob)


def test_book_jobs_remain_on_the_board(bus: Bus) -> None:
    first = bus.publish(book_job(BookOp.CREATE, name="a"))
    bus.publish(book_job(BookOp.UPDATE, name="missing-id"))
    history = bus.list(ManageBookJob, book="items")
    assert [job.id for job in history] == [first.id, history[1].id]
    assert [job.status for job in history] == [JobStatus.COMPLETED, JobStatus.FAILED]
    assert bus.get(ManageBookJob, first.id, book="items").payload["name"] == "a"


def test_book_is_not_on_the_public_surface() -> None:
    import magi.new_bus as bus_pkg

    assert "Book" not in bus_pkg.__all__
    assert not hasattr(bus_pkg.Bus, "book")
    assert not hasattr(bus_pkg.Bus, "get_book")


def test_external_code_uses_jobs_not_books(bus: Bus) -> None:
    bus.publish(book_job(BookOp.CREATE, name="via-job"))
    listed = bus.publish(book_job(BookOp.READ))
    assert listed.result["records"][0]["name"] == "via-job"
    assert not isinstance(listed, Book)


def test_work_jobs_do_not_touch_books(bus: Bus) -> None:
    bus.publish(PingJob(payload={"hello": True}))
    empty = bus.publish(book_job(BookOp.READ))
    assert empty.result["records"] == []
