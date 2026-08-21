"""Sample BaseJob types for tests. Not Firmware."""

from dataclasses import dataclass

from sqlalchemy import Integer
from sqlalchemy.orm import Mapped, mapped_column

from ..base.BaseJob import BaseJob, BaseJobBoard, BaseJobResult, BaseJobRow
from ..base.heartbeat import Slot

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


def occupy(bus, worker_id: str = WORKER) -> None:
    """Take every slot exposed by mounted test boards."""
    for job_type in bus.jobs:
        bus.attach(
            worker_id,
            (
                Slot(job_type, "publish"),
                Slot(job_type, "claim"),
                Slot(job_type, "submit_result"),
            ),
        )
