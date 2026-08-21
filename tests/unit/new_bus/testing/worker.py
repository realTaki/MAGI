"""Worker-view fixtures for exercising the public BUS surface."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from magi.new_bus import Bus, JobBoardClient, WorkerBus, job_board
from magi.new_bus.base.BaseJob import BaseJob, BaseJobBoard, BaseJobResult


def attach_board(
    bus: Bus,
    board_cls: type[BaseJobBoard[Any, Any, Any]],
    *,
    worker_id: str,
    slots: Iterable[str],
) -> JobBoardClient[BaseJob, BaseJobResult]:
    binding = job_board(board_cls, slots=slots)
    worker_type = type("TestWorkerBus", (WorkerBus,), {"jobs": binding})
    worker = bus.for_worker(worker_id, worker_type)
    assert worker.attach()
    return worker.jobs
