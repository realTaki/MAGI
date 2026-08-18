"""Sample BaseJob types for tests. Not Firmware."""

from ..base.job import BaseJob
from ..base.manageBookJob import BookOp, ManageBookJob


class PingJob(BaseJob):
    pass


def book_job(op: BookOp, **payload) -> ManageBookJob:
    return ManageBookJob(book="items", op=op, payload=payload)
