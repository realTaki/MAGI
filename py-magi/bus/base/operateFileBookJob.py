"""Base JobBoard for BUS-owned operations on an internal file Book."""

from __future__ import annotations

from dataclasses import replace

from .BaseJob import BaseJob, BaseJobBoard, BaseJobResult, BaseJobRow


class OperateFileBookJobBoard[JobT: BaseJob, ResultT: BaseJobResult, RowT: BaseJobRow](
    BaseJobBoard[JobT, ResultT, RowT]
):
    """File-Book Jobs execute inside ``publish``. Workers do not claim them."""

    def publish(self, job: JobT) -> int:
        job_id = self._publish(job)
        result = self._execute(replace(job, id=job_id))
        with self._session() as session:
            row = session.get_one(type(self).row_cls, job_id)
            self._write_result(row, result)
            session.commit()
        return job_id

    def _execute(self, job: JobT) -> ResultT:
        """Operate on the file Book. Firmware boards implement this."""
        raise NotImplementedError
