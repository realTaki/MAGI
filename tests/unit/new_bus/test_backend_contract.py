from __future__ import annotations

import pytest

from magi.new_bus import FileBackend, SQLiteBackend
from magi.new_bus.base.backend import Backend
from magi.new_bus.errors import BackendError
from magi.new_bus.testing import InMemoryBackend


def test_insert_get_replace_delete(backend: Backend) -> None:
    store = backend.records("items")
    stored = store.insert({"id": "a", "name": "alpha", "created_at": "1"})
    assert stored["name"] == "alpha"
    assert store.get("a")["name"] == "alpha"

    replaced = store.replace("a", {"id": "a", "name": "beta", "created_at": "1"})
    assert replaced["name"] == "beta"
    assert store.delete("a") is True
    assert store.get("a") is None
    assert store.delete("a") is False


def test_find_orders_and_filters(backend: Backend) -> None:
    store = backend.records("items")
    store.insert({"id": "b", "name": "two", "kind": "x", "created_at": "2"})
    store.insert({"id": "a", "name": "one", "kind": "x", "created_at": "1"})
    store.insert({"id": "c", "name": "three", "kind": "y", "created_at": "3"})

    names = [record["name"] for record in store.find(eq={"kind": "x"})]
    assert names == ["one", "two"]


def test_compare_and_set(backend: Backend) -> None:
    store = backend.records("jobs")
    store.insert({"id": "j1", "status": "pending", "created_at": "1"})
    assert (
        store.compare_and_set("j1", field="status", expect="claimed", update={"status": "x"})
        is None
    )
    won = store.compare_and_set(
        "j1",
        field="status",
        expect="pending",
        update={"status": "claimed"},
    )
    assert won is not None
    assert won["status"] == "claimed"
    assert store.get("j1")["status"] == "claimed"


def test_transaction_rollback_restores_records(backend: Backend) -> None:
    store = backend.records("items")
    store.insert({"id": "keep", "name": "ok", "created_at": "1"})
    with pytest.raises(RuntimeError):
        with backend.transaction():
            store.insert({"id": "gone", "name": "temp", "created_at": "2"})
            store.replace("keep", {"id": "keep", "name": "mutated", "created_at": "1"})
            raise RuntimeError("boom")
    assert store.get("gone") is None
    assert store.get("keep")["name"] == "ok"


def test_duplicate_insert_rejected(backend: Backend) -> None:
    store = backend.records("items")
    store.insert({"id": "a", "created_at": "1"})
    with pytest.raises(BackendError):
        store.insert({"id": "a", "created_at": "2"})


def test_file_and_sqlite_survive_reopen(tmp_path) -> None:
    file_root = tmp_path / "files"
    sqlite_path = tmp_path / "reopen.sqlite"
    for factory in (
        lambda: FileBackend(file_root),
        lambda: SQLiteBackend(sqlite_path),
    ):
        backend = factory()
        backend.records("items").insert({"id": "a", "name": "kept", "created_at": "1"})
        backend.close()
        backend = factory()
        try:
            assert backend.records("items").get("a")["name"] == "kept"
        finally:
            backend.close()


def test_memory_backend_is_not_an_official_backend() -> None:
    import magi.new_bus.base.backends as official

    assert not hasattr(official, "InMemoryBackend")
    assert InMemoryBackend.__module__.endswith("testing.in_memory")
