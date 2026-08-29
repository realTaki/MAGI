"""Sample job types shared by the BUS tests."""

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import Integer
from sqlalchemy.orm import Mapped, mapped_column

from bus import Bus
from bus.base.BaseJob import BaseJob, BaseJobBoard, BaseJobResult, BaseJobRow

WORKER = "test"


@dataclass
class PingJob(BaseJob):
    n: int = 0


class PingJobRow(BaseJobRow):
    __tablename__ = "jobs_PingJob"

    n: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class PingJobBoard(BaseJobBoard[PingJob, BaseJobResult, PingJobRow]):
    job_cls = PingJob
    result_cls = BaseJobResult
    row_cls = PingJobRow


class PingBus(Bus):
    """BUS fixture with the test-only PingJobBoard preconfigured."""

    def __init__(self, workspace: str | Path) -> None:
        super().__init__(workspace)
        self._job_boards[PingJob] = PingJobBoard(self._factory, self._heartbeat)
