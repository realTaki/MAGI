"""Base JobBoard for BUS-owned operations on an internal file Book."""

from __future__ import annotations

from .BaseJob import BaseJob, BaseJobBoard, BaseJobResult, BaseJobRow


class OperateFileBookJobBoard[JobT: BaseJob, ResultT: BaseJobResult, RowT: BaseJobRow](
    BaseJobBoard[JobT, ResultT, RowT]
):
    """File-Book operations execute inside ``publish`` and return their result."""

    def publish(self, job: JobT) -> ResultT:
        return self._execute(job)

    def _execute(self, job: JobT) -> ResultT:
        """Operate on the file Book. Firmware boards implement this."""
        raise NotImplementedError
