from __future__ import annotations

import pytest

from magi.new_bus import FileBackend, SQLiteBackend
from magi.new_bus.base.backends import Backend
from magi.new_bus.base.errors import BackendError
from magi.new_bus.testing import InMemoryBackend


def test_ensure_is_idempotent(backend: Backend) -> None:
    backend.ensure()
    backend.ensure()
    store = backend.records("items")
    stored = store.insert({"name": "after-ensure"})
    assert store.get(stored["id"])["name"] == "after-ensure"


def test_insert_assigns_integer_id(backend: Backend) -> None:
    store = backend.records("items")
    first = store.insert({"name": "alpha", "created_at": "1"})
    second = store.insert({"name": "beta", "created_at": "2"})
    assert first["id"] == 1
    assert second["id"] == 2
    assert store.get(1)["name"] == "alpha"


def test_insert_get_replace_delete(backend: Backend) -> None:
    store = backend.records("items")
    stored = store.insert({"id": 1, "name": "alpha", "created_at": "1"})
    assert stored["name"] == "alpha"
    assert store.get(1)["name"] == "alpha"

    replaced = store.replace(1, {"id": 1, "name": "beta", "created_at": "1"})
    assert replaced["name"] == "beta"
    assert store.delete(1) is True
    assert store.get(1) is None
    assert store.delete(1) is False


def test_find_orders_and_filters(backend: Backend) -> None:
    store = backend.records("items")
    store.insert({"id": 2, "name": "two", "kind": "x", "created_at": "2"})
    store.insert({"id": 1, "name": "one", "kind": "x", "created_at": "1"})
    store.insert({"id": 3, "name": "three", "kind": "y", "created_at": "3"})

    names = [record["name"] for record in store.find(eq={"kind": "x"})]
    assert names == ["one", "two"]


def test_compare_and_set(backend: Backend) -> None:
    store = backend.records("jobs")
    store.insert({"id": 1, "status": "pending", "created_at": "1"})
    assert (
        store.compare_and_set(1, field="status", expect="claimed", update={"status": "x"}) is None
    )
    won = store.compare_and_set(
        1,
        field="status",
        expect="pending",
        update={"status": "claimed"},
    )
    assert won is not None
    assert won["status"] == "claimed"
    assert store.get(1)["status"] == "claimed"


def test_transaction_rollback_restores_records(backend: Backend) -> None:
    store = backend.records("items")
    store.insert({"id": 1, "name": "ok", "created_at": "1"})
    with pytest.raises(RuntimeError):
        with backend.transaction():
            store.insert({"id": 2, "name": "temp", "created_at": "2"})
            store.replace(1, {"id": 1, "name": "mutated", "created_at": "1"})
            raise RuntimeError("boom")
    assert store.get(2) is None
    assert store.get(1)["name"] == "ok"


def test_duplicate_insert_rejected(backend: Backend) -> None:
    store = backend.records("items")
    store.insert({"id": 1, "created_at": "1"})
    with pytest.raises(BackendError):
        store.insert({"id": 1, "created_at": "2"})


def test_file_and_sqlite_survive_reopen(tmp_path) -> None:
    file_root = tmp_path / "files"
    sqlite_path = tmp_path / "reopen.sqlite"
    for factory in (
        lambda: FileBackend(file_root),
        lambda: SQLiteBackend(sqlite_path),
    ):
        backend = factory()
        backend.records("items").insert({"id": 1, "name": "kept", "created_at": "1"})
        backend.close()
        backend = factory()
        try:
            assert backend.records("items").get(1)["name"] == "kept"
        finally:
            backend.close()


def test_memory_backend_is_not_an_official_backend() -> None:
    import magi.new_bus.base.backends as official

    assert not hasattr(official, "InMemoryBackend")
    assert InMemoryBackend.__module__.endswith("testing.in_memory")
