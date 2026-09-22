"""Base JobBoard for direct BUS-owned operations on an internal Book."""

from __future__ import annotations

from .BaseBook import BaseBook
from .BaseJob import BaseJob, BaseJobBoard, BaseJobResult, BaseJobRow
from .engine import EngineFactory


class OperateBookJobBoard[JobT: BaseJob, ResultT: BaseJobResult, RowT: BaseJobRow](
    BaseJobBoard[JobT, ResultT, RowT]
):
    """A Book-operation board: ``publish`` returns :meth:`_execute` directly."""

    def __init__(self, factory: EngineFactory, *, book: BaseBook) -> None:
        super().__init__(factory)
        self._book = book

    def publish(self, job: JobT) -> ResultT:
        return self._execute(job)

    def _execute(self, job: JobT) -> ResultT:
        """Operate on the Book. Firmware boards implement this."""
        raise NotImplementedError
