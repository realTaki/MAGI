"""Sample BaseJob types for tests. Not Firmware."""

from dataclasses import dataclass

from ..base.BaseBook import BaseBook, BaseRecord
from ..base.BaseJob import BaseJob
from ..base.manageBookJob import BookOp, ManageBookJob


class PingJob(BaseJob):
    pass


@dataclass(kw_only=True)
class Item(BaseRecord):
    name: str = ""
    kind: str = ""


class ItemBook(BaseBook):
    record_cls = Item


def book_job(op: BookOp, **payload) -> ManageBookJob:
    return ManageBookJob(book="items", op=op, payload=payload)
