"""Sample BaseJob types for tests. Not Firmware."""

from dataclasses import dataclass

from sqlalchemy import Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..base.BaseBook import BaseBook, BaseRecord, BaseRecordMixin
from ..base.BaseJob import BaseJob, BaseJobBoard, BaseJobResult, BaseJobRow
from ..base.errors import InvalidJobError
from ..base.openBookJob import (
    BookOp,
    OpenBookJob,
    OpenBookJobBoard,
    OpenBookJobResult,
    OpenBookJobRow,
)
from ..firmware.jobs.operateBookJob import OperateBookJobBoard

WORKER = "test"


@dataclass
class PingJob(BaseJob):
    n: int = 0


class PingJobRow(BaseJobRow):
    __tablename__ = "jobs_PingJob"

    n: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class PingJobBoard(BaseJobBoard[PingJob, BaseJobResult, PingJobRow]):
    job_cls = PingJob
    result_cls = BaseJobResult
    row_cls = PingJobRow


@dataclass(kw_only=True)
class Item(BaseRecord):
    name: str = ""
    kind: str = ""


class ItemRow(BaseRecordMixin):
    __tablename__ = "books_items"

    name: Mapped[str] = mapped_column(Text, nullable=False, default="")
    kind: Mapped[str] = mapped_column(Text, nullable=False, default="")


class ItemBook(BaseBook[Item]):
    record_cls = Item
    row_cls = ItemRow


@dataclass
class OpenItemJob(OpenBookJob[Item]):
    pass


@dataclass
class OpenItemJobResult(OpenBookJobResult[Item]):
    pass


class OpenItemJobRow(OpenBookJobRow):
    __tablename__ = "jobs_book_Item"


class OpenItemJobBoard(OpenBookJobBoard[Item]):
    job_cls = OpenItemJob
    result_cls = OpenItemJobResult
    book_cls = ItemBook
    row_cls = OpenItemJobRow


def book_job(op: BookOp, record: Item | None = None, **values) -> OpenItemJob:
    filt = values.pop("filter", None)
    if record is None and values:
        record = Item(**values)
    return OpenItemJob(op=op, record=record, filter=filt)


def occupy(bus, worker_id: str = WORKER) -> None:
    """Take every slot exposed by mounted test boards, plus Book publish slots."""
    for job_type in bus.jobs:
        board = bus.job_board(job_type)
        slots = (
            ("publish",)
            if isinstance(board, OperateBookJobBoard)
            else (
                "publish",
                "claim",
                "submit_result",
            )
        )
        bus.attach(worker_id, job_type, slots)
    for book in bus.books:
        try:
            bus.attach(worker_id, OpenBookJob, ("publish",), book=book)
        except InvalidJobError:
            continue
