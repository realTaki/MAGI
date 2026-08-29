"""Base JobBoard for BUS-owned operations on an internal file Book."""

from __future__ import annotations

from typing import cast

from sqlalchemy import update

from .BaseJob import BaseJob, BaseJobBoard, BaseJobResult, BaseJobRow, JobStatus, error_message


class OperateFileBookJobBoard[JobT: BaseJob, ResultT: BaseJobResult, RowT: BaseJobRow](
    BaseJobBoard[JobT, ResultT, RowT]
):
    """Execute file-Book Jobs inside BUS when their result is requested.

    File Books cannot share the Job row's SQL transaction, but the Book remains
    entirely BUS-private: only this Board invokes its public methods.
    """

    def _claim(self) -> JobT | None:
        return None

    def _submit_result(self, result: ResultT) -> bool:
        del result
        return False

    def get_result(self, job_id: int) -> ResultT | None:
        self.release_idle_slots()
        self._execute_pending(job_id)
        return super().get_result(job_id)

    def _execute(self, job: JobT) -> ResultT:
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
            job = cast(type[JobT], self.job_cls).from_row(row)
            session.commit()

        try:
            result = self._execute(job)
        except Exception as error:  # noqa: BLE001 -- make file failures durable results
            result = type(self).result_cls(status=JobStatus.FAILED, error=error_message(error))

        with self._session() as session:
            row = session.get(row_cls, job_id)
            if row is None or row.status != JobStatus.EXECUTING.value:
                return
            self._write_result(row, result)
            session.commit()
