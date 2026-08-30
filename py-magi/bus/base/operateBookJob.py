"""Base JobBoard for BUS-owned operations on an internal Book.

These jobs do not have a worker ``claim`` phase. They can use the ordinary
``claim_post_publish`` gate. Once it is PENDING, requesting its result executes
the Book operation;
the Book mutation and terminal result are then committed in one transaction.
"""

from __future__ import annotations

from typing import cast

from sqlalchemy import update
from sqlalchemy.orm import Session

from .BaseJob import BaseJob, BaseJobBoard, BaseJobResult, BaseJobRow, JobStatus, error_message
from .slot import SlotTag, slots


class OperateBookJobBoard[JobT: BaseJob, ResultT: BaseJobResult, RowT: BaseJobRow](
    BaseJobBoard[JobT, ResultT, RowT]
):
    """A transactionally executed Book-operation board with no worker claim."""

    def _claim(self) -> JobT | None:
        """Book operations execute in the BUS and therefore cannot be claimed."""
        return None

    def _submit_result(self, result: BaseJobResult) -> bool:
        """Book operations have no worker result to submit."""
        del result
        return False

    def get_result(self, job_id: int) -> ResultT | None:
        self._execute_pending(job_id)
        return super().get_result(job_id)

    def _execute(self, session: Session, job: JobT) -> ResultT:
        """Operate on the Book in the transaction that owns the terminal result."""
        raise NotImplementedError

    def _execute_pending(self, job_id: int) -> None:
        row_cls = type(self).row_cls
        with self._session() as session:
            claimed = session.execute(
                update(row_cls)
                .where(row_cls.id == job_id, row_cls.status == JobStatus.PENDING.value)
                .values(status=JobStatus.EXECUTING.value)
            )
            if getattr(claimed, "rowcount", 0) != 1:
                return
            row = session.get(row_cls, job_id)
            if row is None:
                return
            try:
                result = self._execute(session, cast(type[JobT], self.job_cls).from_row(row))
            except Exception as error:
                session.rollback()
                row = session.get(row_cls, job_id)
                if row is None:
                    return
                result = type(self).result_cls(
                    status=JobStatus.FAILED,
                    error=error_message(error),
                )
            tag = SlotTag(type(self).job_cls, "claim_post_result")
            settling = slots.held(self, tag)
            self._write_result(
                row,
                result,
                status=JobStatus.SETTLING if settling else None,
            )
            session.commit()
            if settling:
                slots.get(self, tag).offer(self, int(row.id))
