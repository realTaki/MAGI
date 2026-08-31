"""Base JobBoard for BUS-owned operations on an internal Book.

These jobs have no worker ``claim`` phase. ``publish`` waits for the
post-publish gate, then executes the Book operation. ``get_result``
awaits that written result. The Job row lives in the logs store; the
Book mutation uses the memories store.
"""

from __future__ import annotations

from dataclasses import replace

from sqlalchemy.orm import Session

from .BaseBook import BaseBook
from .BaseJob import BaseJob, BaseJobBoard, BaseJobResult, BaseJobRow, JobStatus
from .engine import EngineFactory
from .go import go


class OperateBookJobBoard[JobT: BaseJob, ResultT: BaseJobResult, RowT: BaseJobRow](
    BaseJobBoard[JobT, ResultT, RowT]
):
    """A Book-operation board with no worker claim."""

    def __init__(self, factory: EngineFactory, *, book: BaseBook) -> None:
        super().__init__(factory)
        self._book = book

    def _claim(self) -> JobT | None:
        """Book operations execute in the BUS and therefore cannot be claimed."""
        return None

    def _submit_result(self, result: BaseJobResult) -> bool:
        """Book operations have no worker result to submit."""
        del result
        return False

    def publish(self, job: JobT) -> int:
        job_id = self._publish(job)
        published = replace(job, id=job_id)
        if go(self._post_publish(published)).result() is not JobStatus.PENDING:
            return job_id

        with self._book._session() as books:
            result = self._execute(books, published)
            books.commit()

        with self._session() as session:
            row = session.get_one(type(self).row_cls, job_id)
            self._write_result(row, result)
            session.commit()
        return job_id

    def _execute(self, session: Session, job: JobT) -> ResultT:
        """Operate on the Book in the memories store."""
        raise NotImplementedError
