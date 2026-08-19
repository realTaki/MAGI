from __future__ import annotations

import pytest

from magi.new_bus import BookOp, Bus, InvalidJobError, JobStatus, ManageBookJob
from magi.new_bus.base.BaseBook import BaseBook
from magi.new_bus.testing import PingJob, book_job


def test_create_read_update_delete(bus: Bus) -> None:
    created = bus.publish(book_job(BookOp.CREATE, name="alpha", kind="x"))
    assert bus.get_result(created).status is JobStatus.COMPLETED
    record = bus.get_result(created).record
    record_id = record["id"]
    assert record["name"] == "alpha"

    by_id = bus.publish(book_job(BookOp.READ, id=record_id))
    assert bus.get_result(by_id).status is JobStatus.COMPLETED
    assert bus.get_result(by_id).record["name"] == "alpha"

    listed = bus.publish(book_job(BookOp.READ, filter={"kind": "x"}))
    assert bus.get_result(listed).status is JobStatus.COMPLETED
    assert [item["id"] for item in bus.get_result(listed).records] == [record_id]

    updated = bus.publish(book_job(BookOp.UPDATE, id=record_id, name="beta"))
    assert bus.get_result(updated).status is JobStatus.COMPLETED
    assert bus.get_result(updated).record["name"] == "beta"

    deleted = bus.publish(book_job(BookOp.DELETE, id=record_id))
    assert bus.get_result(deleted).status is JobStatus.COMPLETED
    missing = bus.publish(book_job(BookOp.READ, id=record_id))
    assert bus.get_result(missing).status is JobStatus.FAILED
    assert bus.get_result(missing).error


def test_failed_mutation_leaves_book_valid(bus: Bus) -> None:
    created = bus.publish(book_job(BookOp.CREATE, name="keep"))
    record_id = bus.get_result(created).record["id"]

    failed = bus.publish(book_job(BookOp.UPDATE, name="no-id"))
    assert bus.get_result(failed).status is JobStatus.FAILED

    still = bus.publish(book_job(BookOp.READ, id=record_id))
    assert bus.get_result(still).status is JobStatus.COMPLETED
    assert bus.get_result(still).record["name"] == "keep"

    missing = bus.publish(book_job(BookOp.DELETE, id=999))
    assert bus.get_result(missing).status is JobStatus.FAILED
    still = bus.publish(book_job(BookOp.READ, id=record_id))
    assert bus.get_result(still).status is JobStatus.COMPLETED


def test_book_jobs_are_not_claimed(bus: Bus) -> None:
    bus.publish(book_job(BookOp.CREATE, name="x"))
    with pytest.raises(InvalidJobError):
        bus.claim(ManageBookJob)


def test_book_jobs_remain_on_the_board(bus: Bus) -> None:
    first = bus.publish(book_job(BookOp.CREATE, name="a"))
    bus.publish(book_job(BookOp.UPDATE, name="missing-id"))
    history = bus.list(ManageBookJob, book="Item")
    assert [job.id for job in history] == [first.id, history[1].id]
    assert [bus.get_result(job).status for job in history] == [JobStatus.COMPLETED, JobStatus.FAILED]
    loaded = next(job for job in history if job.id == first.id)
    assert loaded.values["name"] == "a"


def test_book_is_not_on_the_public_surface() -> None:
    import magi.new_bus as bus_pkg

    assert "BaseBook" not in bus_pkg.__all__
    assert not hasattr(bus_pkg.Bus, "book")
    assert not hasattr(bus_pkg.Bus, "get_book")


def test_external_code_uses_jobs_not_books(bus: Bus) -> None:
    bus.publish(book_job(BookOp.CREATE, name="via-job"))
    listed = bus.publish(book_job(BookOp.READ))
    assert bus.get_result(listed).records[0]["name"] == "via-job"
    assert not isinstance(listed, BaseBook)


def test_work_jobs_do_not_touch_books(bus: Bus) -> None:
    bus.publish(PingJob())
    empty = bus.publish(book_job(BookOp.READ))
    assert bus.get_result(empty).records == []
