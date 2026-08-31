"""Fixtures for exercising the public BUS JobBoard surface."""

from __future__ import annotations

import time
from typing import Any

from bus import Bus
from bus.base.BaseJob import BaseJob, BaseJobBoard, BaseJobResult


def attach_board(
    bus: Bus,
    board_cls: type[BaseJobBoard[Any, Any, Any]],
) -> BaseJobBoard[BaseJob, BaseJobResult, Any]:
    board = bus.board(board_cls.job_cls)
    return board


def wait_result(board, job_id: int, *, timeout: float = 2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = board.get_result(job_id)
        if result is not None:
            return result
        time.sleep(0.01)
    return board.get_result(job_id)

