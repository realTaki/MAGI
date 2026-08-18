"""Sample Job types for tests. Not Firmware."""

from ..base.job import BookOp, Job, ManageBookJob


class PingJob(Job):
    pass


def book_job(op: BookOp, **payload) -> ManageBookJob:
    return ManageBookJob(book="items", op=op, payload=payload)
