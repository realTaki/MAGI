"""Base JobBoard for BUS-owned operations on an internal Book.

These jobs have no worker claim phase. ``publish`` executes the Book
mutation on the calling thread and writes the result before returning
the Job id.
"""

from __future__ import annotations

from dataclasses import replace

from .BaseBook import BaseBook
from .BaseJob import BaseJob, BaseJobBoard, BaseJobResult, BaseJobRow
from .engine import EngineFactory


class OperateBookJobBoard[JobT: BaseJob, ResultT: BaseJobResult, RowT: BaseJobRow](
    BaseJobBoard[JobT, ResultT, RowT]
):
    """A Book-operation board: ``publish`` runs :meth:`_execute` immediately."""

    def __init__(self, factory: EngineFactory, *, book: BaseBook) -> None:
        super().__init__(factory)
        self._book = book

    def publish(self, job: JobT) -> int:
        job_id = self._publish(job)
        result = self._execute(replace(job, id=job_id))
        with self._session() as session:
            row = session.get_one(type(self).row_cls, job_id)
            self._write_result(row, result)
            session.commit()
        return job_id

    def _execute(self, job: JobT) -> ResultT:
        """Operate on the Book. Firmware boards implement this."""
        raise NotImplementedError
