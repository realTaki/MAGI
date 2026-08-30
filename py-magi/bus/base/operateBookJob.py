"""Base JobBoard for BUS-owned operations on an internal Book.

These jobs have no worker ``claim`` phase. ``publish`` runs the
post-publish gate, then the BUS executes the Book operation.
``get_result`` is a read. The Job row lives in the logs store; the
Book mutation uses the memories store.
"""

from __future__ import annotations

from typing import cast

from sqlalchemy import update
from sqlalchemy.orm import Session

from .BaseJob import BaseJob, BaseJobBoard, BaseJobResult, BaseJobRow, JobStatus, error_message


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

    def _publish(self, job: JobT) -> int:
        job_id = super()._publish(job)
        self._execute_pending(job_id)
        return job_id

    def _execute(self, session: Session, job: JobT) -> ResultT:
        """Operate on the Book in the memories store."""
        raise NotImplementedError

    def _execute_pending(self, job_id: int) -> None:
        row_cls = type(self).row_cls
        with self._session() as session:
            claimed = session.execute(
                update(row_cls)
                .where(row_cls.id == job_id, row_cls.status == JobStatus.PENDING.value)
                .values(status=JobStatus.CLAIMED.value)
            )
            if getattr(claimed, "rowcount", 0) != 1:
                return
            row = session.get(row_cls, job_id)
            if row is None:
                return
            job = cast(type[JobT], self.job_cls).from_row(row)
            session.commit()

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
            if row is None or row.status != JobStatus.CLAIMED.value:
                return
            self._write_result(row, result)
            session.commit()
