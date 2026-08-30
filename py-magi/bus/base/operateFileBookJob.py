"""Base JobBoard for BUS-owned operations on an internal file Book."""

from __future__ import annotations

from dataclasses import replace
from typing import cast

from .BaseJob import BaseJob, BaseJobBoard, BaseJobResult, BaseJobRow, JobStatus, error_message
from .go import go


class OperateFileBookJobBoard[JobT: BaseJob, ResultT: BaseJobResult, RowT: BaseJobRow](
    BaseJobBoard[JobT, ResultT, RowT]
):
    """Execute file-Book Jobs inside BUS after publish.

    File Books cannot share the Job row's SQL transaction, but the Book remains
    entirely BUS-private: only this Board invokes its public methods.
    ``get_result`` is a read.
    """

    def _claim(self) -> JobT | None:
        return None

    def _submit_result(self, result: ResultT) -> bool:
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

        try:
            result = self._execute(job)
        except Exception as error:  # noqa: BLE001 -- make file failures durable results
            result = type(self).result_cls(status=JobStatus.FAILED, error=error_message(error))

        with self._session() as session:
            row = session.get(row_cls, job_id)
            if row is None:
                return job_id
            self._write_result(row, result)
            session.commit()
        return job_id

    def _execute(self, job: JobT) -> ResultT:
        raise NotImplementedError
