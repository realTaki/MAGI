"""Sample BaseJob types for tests. Not Firmware."""

from dataclasses import dataclass

from sqlalchemy import Text
from sqlalchemy.orm import Mapped, mapped_column

from ..base.BaseBook import BaseBook
from ..base.BaseJob import BaseJob
from ..base.BaseRecord import BaseRecord, BaseRecordMixin
from ..base.manageBookJob import BookOp, ManageBookJob


@dataclass
class PingJob(BaseJob):
    n: int = 0


@dataclass(kw_only=True)
class Item(BaseRecord):
    name: str = ""
    kind: str = ""


class ItemRow(BaseRecordMixin):
    __tablename__ = "books_items"

    name: Mapped[str] = mapped_column(Text, nullable=False, default="")
    kind: Mapped[str] = mapped_column(Text, nullable=False, default="")


class ItemBook(BaseBook):
    name = "items"
    record_cls = Item
    row_cls = ItemRow


def book_job(op: BookOp, **values) -> ManageBookJob:
    record_id = values.pop("id", 0) or values.pop("record_id", 0) or 0
    filt = values.pop("filter", None)
    return ManageBookJob(
        book="items",
        op=op,
        record_id=int(record_id),
        filter=filt,
        values=values,
    )
