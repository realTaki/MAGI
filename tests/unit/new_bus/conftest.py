from __future__ import annotations

from collections.abc import Iterator

import pytest

from magi.new_bus import Bus, FileBackend, SQLiteBackend
from magi.new_bus.base.backends import Backend
from magi.new_bus.testing import InMemoryBackend, ItemBook, PingJob


@pytest.fixture(params=["memory", "file", "sqlite"])
def backend(request: pytest.FixtureRequest, tmp_path) -> Iterator[Backend]:
    if request.param == "memory":
        store: Backend = InMemoryBackend()
    elif request.param == "file":
        store = FileBackend(tmp_path / "file-backend")
    else:
        store = SQLiteBackend(tmp_path / "bus.sqlite")
    try:
        yield store
    finally:
        store.close()


@pytest.fixture
def bus(backend: Backend) -> Iterator[Bus]:
    with Bus(backend) as item:
        item.mount_book("items", book_cls=ItemBook)
        item.mount_job(PingJob)
        yield item
