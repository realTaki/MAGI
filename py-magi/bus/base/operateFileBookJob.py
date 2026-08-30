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
    ``get_result`` is a read.
    """

    def _claim(self) -> JobT | None:
        return None

    def _submit_result(self, result: BaseJobResult) -> bool:
        """File-Book operations execute inline during publish, so the
        worker-facing submit path is a no-op."""
        del result
        return False

    def publish(self, job: JobT) -> int:
        job_id = self._publish(job)
        published = replace(job, id=job_id)
        if go(self._post_publish(published)).result() is not JobStatus.PENDING:
            return job_id

        result = self._execute(published)

        with self._session() as session:
            row = session.get_one(type(self).row_cls, job_id)
            self._write_result(row, result)
            session.commit()
        return job_id

    def _execute(self, job: JobT) -> ResultT:
        raise NotImplementedError
