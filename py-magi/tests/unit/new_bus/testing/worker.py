"""Fixtures for exercising the public BUS JobBoard surface."""

from __future__ import annotations

from typing import Any

from bus import Bus, go
from bus.base.BaseJob import BaseJob, BaseJobBoard, BaseJobResult


def attach_board(
    bus: Bus,
    board_cls: type[BaseJobBoard[Any, Any, Any]],
) -> BaseJobBoard[BaseJob, BaseJobResult, Any]:
    board = bus.board(board_cls.job_cls)
    return board


def wait_result(board, job_id: int, *, timeout: float = 2.0):
    return go(board.get_result(job_id, timeout=timeout)).result()
