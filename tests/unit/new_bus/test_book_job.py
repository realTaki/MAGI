from __future__ import annotations

import pytest

from magi.new_bus import Bus, InvalidJobError, JobStatus
from magi.new_bus.base.BaseBook import BaseBook, BaseRecord
from magi.new_bus.base.openBookJob import BookOp, OpenBookJob, OpenBookJobResult
from magi.new_bus.testing import WORKER, PingJob, book_job

ITEM = "Item"


def _publish[RecordT: BaseRecord](bus: Bus, job: OpenBookJob[RecordT]) -> OpenBookJob[RecordT]:
    job.id = bus.publish(job, worker_id=WORKER, book=ITEM)
    return job


def _result[RecordT: BaseRecord](bus: Bus, job: OpenBookJob[RecordT]) -> OpenBookJobResult[RecordT]:
    result = bus.get_result(job, book=ITEM)
    assert result is not None
    return result


def test_create_read_update_delete(bus: Bus) -> None:
    created = _publish(bus, book_job(BookOp.ADD, name="alpha", kind="x"))
    assert _result(bus, created).status is JobStatus.COMPLETED
    record = _result(bus, created).record
    assert record is not None
    record_id = record.id
    assert record.name == "alpha"

    by_id = _publish(bus, book_job(BookOp.GET, id=record_id))
    assert _result(bus, by_id).status is JobStatus.COMPLETED
    got = _result(bus, by_id).record
    assert got is not None
    assert got.name == "alpha"

    listed = _publish(bus, book_job(BookOp.GET, filter={"kind": "x"}))
    assert _result(bus, listed).status is JobStatus.COMPLETED
    records = _result(bus, listed).records
    assert records is not None
    assert [item.id for item in records] == [record_id]

    updated = _publish(bus, book_job(BookOp.UPDATE, id=record_id, name="beta"))
    assert _result(bus, updated).status is JobStatus.COMPLETED
    updated_record = _result(bus, updated).record
    assert updated_record is not None
    assert updated_record.name == "beta"

    deleted = _publish(bus, book_job(BookOp.DELETE, id=record_id))
    assert _result(bus, deleted).status is JobStatus.COMPLETED
    missing = _publish(bus, book_job(BookOp.GET, id=record_id))
    assert _result(bus, missing).status is JobStatus.FAILED
    assert _result(bus, missing).error


def test_failed_mutation_leaves_book_valid(bus: Bus) -> None:
    created = _publish(bus, book_job(BookOp.ADD, name="keep"))
    record = _result(bus, created).record
    assert record is not None
    record_id = record.id

    failed = _publish(bus, book_job(BookOp.UPDATE, name="no-id"))
    assert _result(bus, failed).status is JobStatus.FAILED

    still = _publish(bus, book_job(BookOp.GET, id=record_id))
    assert _result(bus, still).status is JobStatus.COMPLETED
    kept = _result(bus, still).record
    assert kept is not None
    assert kept.name == "keep"

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
    assert loaded.record is not None
    assert loaded.record.name == "a"


def test_book_is_not_on_the_public_surface() -> None:
    import magi.new_bus as bus_pkg

    assert "BaseBook" not in bus_pkg.__all__
    assert "OpenBookJob" not in bus_pkg.__all__
    assert not hasattr(bus_pkg.Bus, "book")
    assert not hasattr(bus_pkg.Bus, "get_book")


def test_external_code_uses_jobs_not_books(bus: Bus) -> None:
    _publish(bus, book_job(BookOp.ADD, name="via-job"))
    listed = _publish(bus, book_job(BookOp.GET))
    records = _result(bus, listed).records
    assert records is not None
    assert records[0].name == "via-job"
    assert not isinstance(listed, BaseBook)


def test_work_jobs_do_not_touch_books(bus: Bus) -> None:
    bus.publish(PingJob(), worker_id=WORKER)
    empty = _publish(bus, book_job(BookOp.GET))
    assert _result(bus, empty).records == []
