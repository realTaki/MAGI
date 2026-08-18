"""In-memory Backend for tests. Not part of the official backend set."""

from __future__ import annotations

import copy
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from ..base.backends import Backend, RecordStore
from ..base.backends._common import (
    check_collection,
    copy_record,
    matches,
    require_id,
    sort_records,
)
from ..errors import BackendError


class InMemoryBackend(Backend):
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._data: dict[str, dict[str, dict[str, Any]]] = {}
        self._tx_depth = 0
        self._snapshot: dict[str, dict[str, dict[str, Any]]] | None = None

    def records(self, name: str) -> RecordStore:
        return _MemoryStore(self, check_collection(name))

    @contextmanager
    def transaction(self) -> Iterator[None]:
        with self._lock:
            if self._tx_depth == 0:
                self._snapshot = copy.deepcopy(self._data)
            self._tx_depth += 1
            try:
                yield
            except Exception:
                if self._tx_depth == 1 and self._snapshot is not None:
                    self._data = self._snapshot
                raise
            finally:
                self._tx_depth -= 1
                if self._tx_depth == 0:
                    self._snapshot = None

    def close(self) -> None:
        with self._lock:
            self._data.clear()


class _MemoryStore(RecordStore):
    def __init__(self, backend: InMemoryBackend, name: str) -> None:
        self._backend = backend
        self._name = name

    def insert(self, record: Mapping[str, Any]) -> dict[str, Any]:
        data = copy_record(record)
        record_id = require_id(data)
        with self._backend._lock:
            bucket = self._backend._data.setdefault(self._name, {})
            if record_id in bucket:
                raise BackendError(f"duplicate id {record_id}")
            bucket[record_id] = data
            return copy_record(data)

    def get(self, id: str) -> dict[str, Any] | None:
        with self._backend._lock:
            record = self._backend._data.get(self._name, {}).get(id)
            return copy_record(record) if record is not None else None

    def replace(self, id: str, record: Mapping[str, Any]) -> dict[str, Any]:
        data = copy_record(record)
        data["id"] = id
        with self._backend._lock:
            bucket = self._backend._data.setdefault(self._name, {})
            if id not in bucket:
                raise BackendError(f"missing id {id}")
            bucket[id] = data
            return copy_record(data)

    def delete(self, id: str) -> bool:
        with self._backend._lock:
            bucket = self._backend._data.get(self._name)
            if not bucket or id not in bucket:
                return False
            del bucket[id]
            return True

    def find(
        self,
        *,
        status: str | None = None,
        eq: Mapping[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        with self._backend._lock:
            records = [
                copy_record(record)
                for record in self._backend._data.get(self._name, {}).values()
                if (status is None or record.get("status") == status) and matches(record, eq)
            ]
        return sort_records(records)

    def compare_and_set(
        self,
        id: str,
        *,
        field: str,
        expect: Any,
        update: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        with self._backend._lock:
            bucket = self._backend._data.get(self._name, {})
            current = bucket.get(id)
            if current is None or current.get(field) != expect:
                return None
            merged = copy_record(current)
            merged.update(update)
            merged["id"] = id
            bucket[id] = merged
            return copy_record(merged)
