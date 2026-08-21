"""Sample job types shared by the BUS tests."""

from dataclasses import dataclass

from sqlalchemy import Integer
from sqlalchemy.orm import Mapped, mapped_column

from magi.new_bus.base.BaseJob import BaseJob, BaseJobBoard, BaseJobResult, BaseJobRow

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
