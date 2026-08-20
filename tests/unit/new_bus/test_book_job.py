from __future__ import annotations

import pytest

from magi.new_bus import BookOp, Bus, InvalidJobError, JobStatus, OpenBookJob
from magi.new_bus.base.BaseBook import BaseBook
from magi.new_bus.testing import WORKER, PingJob, book_job

ITEM = "Item"


def _publish(bus: Bus, job: OpenBookJob) -> OpenBookJob:
    job.id = bus.publish(job, worker_id=WORKER, book=ITEM)
    return job


def _result(bus: Bus, job: OpenBookJob):
    return bus.get_result(job, book=ITEM)


def test_create_read_update_delete(bus: Bus) -> None:
    created = _publish(bus, book_job(BookOp.ADD, name="alpha", kind="x"))
    assert _result(bus, created).status is JobStatus.COMPLETED
    record = _result(bus, created).record
    record_id = record.id
    assert record.name == "alpha"

    by_id = _publish(bus, book_job(BookOp.GET, id=record_id))
    assert _result(bus, by_id).status is JobStatus.COMPLETED
    assert _result(bus, by_id).record.name == "alpha"

    listed = _publish(bus, book_job(BookOp.GET, filter={"kind": "x"}))
    assert _result(bus, listed).status is JobStatus.COMPLETED
    assert [item.id for item in _result(bus, listed).records] == [record_id]

    updated = _publish(bus, book_job(BookOp.UPDATE, id=record_id, name="beta"))
    assert _result(bus, updated).status is JobStatus.COMPLETED
    assert _result(bus, updated).record.name == "beta"

    deleted = _publish(bus, book_job(BookOp.DELETE, id=record_id))
    assert _result(bus, deleted).status is JobStatus.COMPLETED
    missing = _publish(bus, book_job(BookOp.GET, id=record_id))
    assert _result(bus, missing).status is JobStatus.FAILED
    assert _result(bus, missing).error


def test_failed_mutation_leaves_book_valid(bus: Bus) -> None:
    created = _publish(bus, book_job(BookOp.ADD, name="keep"))
    record_id = _result(bus, created).record.id

    failed = _publish(bus, book_job(BookOp.UPDATE, name="no-id"))
    assert _result(bus, failed).status is JobStatus.FAILED

    still = _publish(bus, book_job(BookOp.GET, id=record_id))
    assert _result(bus, still).status is JobStatus.COMPLETED
    assert _result(bus, still).record.name == "keep"

    missing = _publish(bus, book_job(BookOp.DELETE, id=999))
    assert _result(bus, missing).status is JobStatus.FAILED
    still = _publish(bus, book_job(BookOp.GET, id=record_id))
    assert _result(bus, still).status is JobStatus.COMPLETED


def test_book_jobs_are_not_claimed(bus: Bus) -> None:
    _publish(bus, book_job(BookOp.ADD, name="x"))
    with pytest.raises(InvalidJobError):
        bus.claim(OpenBookJob, worker_id=WORKER)


def test_book_jobs_remain_on_the_board(bus: Bus) -> None:
    first = book_job(BookOp.ADD, name="a")
    first_id = bus.publish(first, worker_id=WORKER, book=ITEM)
    bus.publish(book_job(BookOp.UPDATE, name="missing-id"), worker_id=WORKER, book=ITEM)
    history = bus.list(OpenBookJob, book=ITEM)
    assert [job.id for job in history] == [first_id, history[1].id]
    assert [_result(bus, job).status for job in history] == [JobStatus.COMPLETED, JobStatus.FAILED]
    loaded = next(job for job in history if job.id == first_id)
    assert loaded.record.name == "a"


def test_book_is_not_on_the_public_surface() -> None:
    import magi.new_bus as bus_pkg

    assert "BaseBook" not in bus_pkg.__all__
    assert not hasattr(bus_pkg.Bus, "book")
    assert not hasattr(bus_pkg.Bus, "get_book")


def test_external_code_uses_jobs_not_books(bus: Bus) -> None:
    _publish(bus, book_job(BookOp.ADD, name="via-job"))
    listed = _publish(bus, book_job(BookOp.GET))
    assert _result(bus, listed).records[0].name == "via-job"
    assert not isinstance(listed, BaseBook)


def test_work_jobs_do_not_touch_books(bus: Bus) -> None:
    bus.publish(PingJob(), worker_id=WORKER)
    empty = _publish(bus, book_job(BookOp.GET))
    assert _result(bus, empty).records == []
