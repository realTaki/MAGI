from __future__ import annotations

from collections.abc import Iterator

import pytest

from magi.new_bus import Bus, SQLiteBackend
from magi.new_bus.base.engine import EngineFactory
from tests.unit.new_bus.testing import WORKER, InMemoryBackend, PingJob, PingJobBoard, attach_board


@pytest.fixture(params=["memory", "sqlite"])
def db_backend(request: pytest.FixtureRequest, tmp_path) -> Iterator[EngineFactory]:
    store = (
        InMemoryBackend() if request.param == "memory" else SQLiteBackend(tmp_path / "bus.sqlite")
    )
    try:
        yield store
    finally:
        store.close()


@pytest.fixture
def bus(db_backend: EngineFactory) -> Iterator[Bus]:
    with Bus(db_backend) as item:
        item.mount_job(PingJob, board_cls=PingJobBoard)
        yield item


@pytest.fixture
def ping_board(bus: Bus):
    return attach_board(
        bus,
        PingJobBoard,
        worker_id=WORKER,
        slots=("publish", "claim", "submit_result"),
    )
