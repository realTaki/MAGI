"""Base JobBoard for BUS-owned operations on an internal Book.

These jobs do not have a worker ``claim`` phase.  They still use the ordinary
``post_publish`` gate: a held checker moves the job through PREPARING and
HOOKING before the BUS executes it.  The Book mutation and terminal result are
then committed in one transaction.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import update
from sqlalchemy.orm import Session

from .BaseJob import BaseJob, BaseJobBoard, BaseJobResult, BaseJobRow, JobStatus, slot
from .errors import InvalidJobError
from .time import utcnow


class OperateBookJobBoard[JobT: BaseJob, ResultT: BaseJobResult, RowT: BaseJobRow](
    BaseJobBoard[JobT, ResultT, RowT]
):
    """A transactionally executed Book-operation board with no worker claim."""

    @slot
    def publish(self, job: JobT, *, worker_id: str) -> int:
        job_id = super().publish(job, worker_id=worker_id)
        if not self._slot_held("post_publish"):
            self._execute_pending(job_id)
        return job_id

    @slot
    def submit_post_publish(self, job: JobT, result: BaseJobResult, *, worker_id: str) -> bool:
        if result.status not in {JobStatus.PENDING, JobStatus.FAILED}:
            raise InvalidJobError("post_publish must return PENDING to approve or FAILED to reject")
        if not super().submit_post_publish(job, result, worker_id=worker_id):
            return False
        if result.status is JobStatus.PENDING:
            self._execute_pending(job.id)
        return True

    def claim(self, *, worker_id: str) -> JobT | None:
        del worker_id
        raise InvalidJobError("Book-operation jobs are executed by BUS and cannot be claimed")

    def submit_result(self, result: BaseJobResult, *, worker_id: str) -> bool:
        del result, worker_id
        raise InvalidJobError("Book-operation jobs complete themselves")

    def get_result(self, job_id: int) -> ResultT | None:
        self.release_idle_slots()
        self._execute_pending(job_id)
        return super().get_result(job_id)

    def check_job_status(self, job_id: int) -> JobStatus | None:
        self.release_idle_slots()
        self._execute_pending(job_id)
        return super().check_job_status(job_id)

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
            result = self._execute(session, self.job_cls.from_row(row))
            self._write_result(row, result)
            session.commit()

    def _write_result(self, row: RowT, result: ResultT) -> None:
        values: dict[str, Any] = result.to_dict()
        values.pop("id", None)
        values.pop("created_at", None)
        values.pop("updated_at", None)
        for key, value in values.items():
            setattr(row, key, value)
        row.status = (
            JobStatus.SETTLING.value if self._slot_held("post_result") else result.status.value
        )
        row.error = result.error
        row.updated_at = utcnow()
