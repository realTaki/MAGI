from __future__ import annotations

from collections.abc import Iterator

import pytest

from magi.new_bus import Bus, FileBackend, SQLiteBackend
from magi.new_bus.base.backends import Backend, DatabaseBackend
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
def db_backend(backend: Backend) -> Backend:
    if not isinstance(backend, DatabaseBackend):
        pytest.skip("needs a database backend")
    return backend


@pytest.fixture
def bus(db_backend: Backend) -> Iterator[Bus]:
    with Bus(db_backend) as item:
        item.mount_book(ItemBook)
        item.mount_job(PingJob)
        yield item
