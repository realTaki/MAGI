"""Sample BaseJob types for tests. Not Firmware."""

from dataclasses import dataclass

from sqlalchemy import Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..base.BaseBook import BaseBook, BaseRecord, BaseRecordMixin
from ..base.BaseJob import BaseJob, BaseJobBoard, BaseJobRow
from ..base.errors import InvalidJobError
from ..base.openBookJob import BookOp, OpenBookJob, OpenBookJobBoard, OpenBookJobRow

WORKER = "test"


@dataclass
class PingJob(BaseJob):
    n: int = 0


class PingJobRow(BaseJobRow):
    __tablename__ = "jobs_PingJob"

    n: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class PingJobBoard(BaseJobBoard):
    job_cls = PingJob
    row_cls = PingJobRow


@dataclass(kw_only=True)
class Item(BaseRecord):
    name: str = ""
    kind: str = ""


class ItemRow(BaseRecordMixin):
    __tablename__ = "books_items"

    name: Mapped[str] = mapped_column(Text, nullable=False, default="")
    kind: Mapped[str] = mapped_column(Text, nullable=False, default="")


class ItemBook(BaseBook):
    record_cls = Item
    row_cls = ItemRow


class OpenItemJobRow(OpenBookJobRow):
    __tablename__ = "jobs_book_Item"


class OpenItemJobBoard(OpenBookJobBoard):
    book_cls = ItemBook
    row_cls = OpenItemJobRow


def book_job(op: BookOp, record: BaseRecord | None = None, **values) -> OpenBookJob:
    filt = values.pop("filter", None)
    if record is None and values:
        record = Item(**values)
    return OpenBookJob(op=op, record=record, filter=filt)


def occupy(bus, worker_id: str = WORKER) -> None:
    """Take publish/claim/submit_result on every mounted work board, and publish on book boards."""
    for job_type in bus.jobs:
        bus.attach(worker_id, job_type, ("publish", "claim", "submit_result"))
    for book in bus.books:
        try:
            bus.attach(worker_id, OpenBookJob, ("publish",), book=book)
        except InvalidJobError:
            continue
