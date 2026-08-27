"""Worker-view fixtures for exercising the public BUS surface."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from magi.new_bus import Bus, JobBoardClient, Slot
from magi.new_bus.base.BaseJob import BaseJob, BaseJobBoard, BaseJobResult


def attach_board(
    bus: Bus,
    board_cls: type[BaseJobBoard[Any, Any, Any]],
    *,
    worker_id: str,
    slots: Iterable[str],
) -> JobBoardClient[BaseJob, BaseJobResult]:
    job_cls = board_cls.job_cls
    worker = bus.for_worker(worker_id, tuple(Slot(job_cls, name) for name in slots))
    assert worker is not None
    return worker.board(job_cls)
