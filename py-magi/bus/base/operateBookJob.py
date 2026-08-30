"""Base JobBoard for BUS-owned operations on an internal Book.

These jobs have no worker ``claim`` phase. ``publish`` waits for the
post-publish gate, then executes the Book operation. ``get_result`` is
a read. The Job row lives in the logs store; the Book mutation uses
the memories store.
"""

from __future__ import annotations

from dataclasses import replace
from typing import cast

from sqlalchemy.orm import Session

from .BaseJob import BaseJob, BaseJobBoard, BaseJobResult, BaseJobRow, JobStatus, error_message
from .go import go


class OperateBookJobBoard[JobT: BaseJob, ResultT: BaseJobResult, RowT: BaseJobRow](
    BaseJobBoard[JobT, ResultT, RowT]
):
    """A Book-operation board with no worker claim."""

    def _claim(self) -> JobT | None:
        """Book operations execute in the BUS and therefore cannot be claimed."""
        return None

    def _submit_result(self, result: BaseJobResult) -> bool:
        """Book operations have no worker result to submit."""
        del result
        return False

    def publish(self, job: JobT) -> int:
        job_id = self._publish(job)
        go(self._post_publish(replace(job, id=job_id))).result()
        row_cls = type(self).row_cls
        with self._session() as session:
            row = session.get(row_cls, job_id)
            if row is None or row.status != JobStatus.PENDING.value:
                return job_id
            job = cast(type[JobT], self.job_cls).from_row(row)

        if self._book is None:
            raise RuntimeError(f"{type(self).__name__} requires a Book")
        try:
            with self._book._session() as books:
                try:
                    result = self._execute(books, job)
                    books.commit()
                except Exception:
                    books.rollback()
                    raise
        except Exception as error:
            result = type(self).result_cls(
                status=JobStatus.FAILED,
                error=error_message(error),
            )

        with self._session() as session:
            row = session.get(row_cls, job_id)
            if row is None:
                return job_id
            self._write_result(row, result)
            session.commit()
        return job_id

    def _execute(self, session: Session, job: JobT) -> ResultT:
        """Operate on the Book in the memories store."""
        raise NotImplementedError
