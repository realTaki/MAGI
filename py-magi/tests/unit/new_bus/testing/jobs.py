"""Sample job types shared by the BUS tests."""

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import Integer
from sqlalchemy.orm import Mapped, mapped_column

from bus import Bus
from bus.base.BaseJob import BaseJob, BaseJobResult, BaseJobRow
from bus.base.hookableJobBoard import HookableJobBoard

WORKER = "test"


@dataclass
class PingJob(BaseJob):
    publisher: str = WORKER
    n: int = 0


class PingJobRow(BaseJobRow):
    __tablename__ = "jobs_PingJob"

    n: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class PingJobBoard(HookableJobBoard[PingJob, BaseJobResult, PingJobRow]):
    job_cls = PingJob
    result_cls = BaseJobResult
    row_cls = PingJobRow


class PingBus(Bus):
    """BUS fixture with the test-only PingJobBoard preconfigured."""

    def __init__(self, workspace: str | Path) -> None:
        super().__init__("@ping", workspace=workspace)
        PingJobRow.__table__.create(self._logs.engine, checkfirst=True)
        self._job_boards[PingJob] = PingJobBoard(self._logs)
