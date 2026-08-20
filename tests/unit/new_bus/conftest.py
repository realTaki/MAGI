from __future__ import annotations

from collections.abc import Iterator

import pytest

from magi.new_bus import Bus, SQLiteBackend
from magi.new_bus.base.engine import EngineFactory
from magi.new_bus.testing import InMemoryBackend, ItemBook, PingJob, PingJobBoard, occupy


@pytest.fixture(params=["memory", "sqlite"])
def db_backend(request: pytest.FixtureRequest, tmp_path) -> Iterator[EngineFactory]:
    store = InMemoryBackend() if request.param == "memory" else SQLiteBackend(tmp_path / "bus.sqlite")
    try:
        yield store
    finally:
        store.close()


@pytest.fixture
def bus(db_backend: EngineFactory) -> Iterator[Bus]:
    with Bus(db_backend) as item:
        item.mount_book(ItemBook)
        item.mount_job(PingJob, board_cls=PingJobBoard)
        occupy(item)
        yield item
