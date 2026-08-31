"""Base JobBoard for BUS-owned operations on an internal file Book."""

from __future__ import annotations

from dataclasses import replace

from .BaseJob import BaseJob, BaseJobBoard, BaseJobResult, BaseJobRow, JobStatus
from .go import go


class OperateFileBookJobBoard[JobT: BaseJob, ResultT: BaseJobResult, RowT: BaseJobRow](
    BaseJobBoard[JobT, ResultT, RowT]
):
    """Execute file-Book Jobs inside BUS after publish.

    File Books cannot share the Job row's SQL transaction, but the Book remains
    entirely BUS-private: only this Board invokes its public methods.
    ``publish`` returns the Job id immediately; ``get_result`` waits for
    the written result.
    """

    def _claim(self) -> JobT | None:
        return None

    def _submit_result(self, result: BaseJobResult) -> bool:
        """Book operations write their own result; workers do not submit."""
        del result
        return False

    def publish(self, job: JobT) -> int:
        job_id = self._publish(job)
        go(self._operate(replace(job, id=job_id)))
        return job_id

    async def _operate(self, job: JobT) -> None:
        if await self._post_publish(job) is not JobStatus.PENDING:
            return
        result = self._execute(job)
        with self._session() as session:
            row = session.get_one(type(self).row_cls, job.id)
            self._write_result(row, result)
            session.commit()

    def _execute(self, job: JobT) -> ResultT:
        raise NotImplementedError
