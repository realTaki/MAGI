"""File Backend — one JSON file per record. Readability over sophistication."""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from ._common import check_collection, coerce_id, copy_record, matches, next_id, sort_records
from .backend import Backend, RecordStore
from .errors import BackendError


class FileBackend(Backend):
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self._lock = threading.RLock()
        self._tx_depth = 0
        self._pending: dict[str, dict[int, dict[str, Any] | None]] = {}
        self.ensure()

    def ensure(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def records(self, name: str, **_spec: Any) -> RecordStore:
        return _FileStore(self, check_collection(name))

    @contextmanager
    def transaction(self) -> Iterator[None]:
        with self._lock:
            self._tx_depth += 1
            try:
                yield
                if self._tx_depth == 1:
                    self._commit()
            except Exception:
                if self._tx_depth == 1:
                    self._pending.clear()
                raise
            finally:
                self._tx_depth -= 1
                if self._tx_depth == 0:
                    self._pending.clear()

    def close(self) -> None:
        return

    def collection_dir(self, name: str) -> Path:
        return self.root / name

    def _path(self, name: str, record_id: int) -> Path:
        return self.collection_dir(name) / f"{record_id}.json"

    def _read_disk(self, name: str, record_id: int) -> dict[str, Any] | None:
        path = self._path(name, record_id)
        if not path.is_file():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise BackendError(f"failed to read {path}") from exc

    def _write_disk(self, name: str, record_id: int, record: Mapping[str, Any]) -> None:
        directory = self.collection_dir(name)
        directory.mkdir(parents=True, exist_ok=True)
        path = self._path(name, record_id)
        tmp = path.with_suffix(".json.tmp")
        try:
            tmp.write_text(
                json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
            )
            os.replace(tmp, path)
        except OSError as exc:
            tmp.unlink(missing_ok=True)
            raise BackendError(f"failed to write {path}") from exc

    def _delete_disk(self, name: str, record_id: int) -> bool:
        path = self._path(name, record_id)
        if not path.is_file():
            return False
        try:
            path.unlink()
        except OSError as exc:
            raise BackendError(f"failed to delete {path}") from exc
        return True

    def _list_disk_ids(self, name: str) -> list[int]:
        directory = self.collection_dir(name)
        if not directory.is_dir():
            return []
        ids: list[int] = []
        for path in directory.glob("*.json"):
            try:
                ids.append(int(path.stem))
            except ValueError:
                continue
        return ids

    def _existing_ids(self, name: str) -> list[int]:
        ids = set(self._list_disk_ids(name))
        for record_id, record in self._pending.get(name, {}).items():
            if record is None:
                ids.discard(record_id)
            else:
                ids.add(record_id)
        return list(ids)

    def _visible(self, name: str, record_id: int) -> dict[str, Any] | None:
        pending = self._pending.get(name)
        if pending is not None and record_id in pending:
            record = pending[record_id]
            return copy_record(record) if record is not None else None
        record = self._read_disk(name, record_id)
        return copy_record(record) if record is not None else None

    def _stage(self, name: str, record_id: int, record: dict[str, Any] | None) -> None:
        self._pending.setdefault(name, {})[record_id] = record

    def _commit(self) -> None:
        for name, records in self._pending.items():
            for record_id, record in records.items():
                if record is None:
                    self._delete_disk(name, record_id)
                else:
                    self._write_disk(name, record_id, record)


class _FileStore(RecordStore):
    def __init__(self, backend: FileBackend, name: str) -> None:
        self._backend = backend
        self._name = name

    @property
    def directory(self) -> Path:
        return self._backend.collection_dir(self._name)

    def path_for(self, record_id: int) -> Path:
        return self._backend._path(self._name, record_id)

    def insert(self, record: Mapping[str, Any]) -> dict[str, Any]:
        data = copy_record(record)
        with self._backend._lock:
            record_id = coerce_id(data.get("id"))
            if record_id is None:
                record_id = next_id(self._backend._existing_ids(self._name))
                data["id"] = record_id
            elif self._backend._visible(self._name, record_id) is not None:
                raise BackendError(f"duplicate id {record_id}")
            if self._backend._tx_depth:
                self._backend._stage(self._name, record_id, data)
            else:
                self._backend._write_disk(self._name, record_id, data)
            return copy_record(data)

    def get(self, id: int) -> dict[str, Any] | None:
        with self._backend._lock:
            return self._backend._visible(self._name, id)

    def replace(self, id: int, record: Mapping[str, Any]) -> dict[str, Any]:
        data = copy_record(record)
        data["id"] = id
        with self._backend._lock:
            if self._backend._visible(self._name, id) is None:
                raise BackendError(f"missing id {id}")
            if self._backend._tx_depth:
                self._backend._stage(self._name, id, data)
            else:
                self._backend._write_disk(self._name, id, data)
            return copy_record(data)

    def delete(self, id: int) -> bool:
        with self._backend._lock:
            if self._backend._visible(self._name, id) is None:
                return False
            if self._backend._tx_depth:
                self._backend._stage(self._name, id, None)
            else:
                self._backend._delete_disk(self._name, id)
            return True

    def find(
        self,
        *,
        status: str | None = None,
        eq: Mapping[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        with self._backend._lock:
            ids = set(self._backend._list_disk_ids(self._name))
            pending = self._backend._pending.get(self._name, {})
            ids.update(pending)
            records: list[dict[str, Any]] = []
            for record_id in ids:
                record = self._backend._visible(self._name, record_id)
                if record is None:
                    continue
                if status is not None and record.get("status") != status:
                    continue
                if not matches(record, eq):
                    continue
                records.append(record)
        return sort_records(records)

    def compare_and_set(
        self,
        id: int,
        *,
        field: str,
        expect: Any,
        update: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        with self._backend._lock:
            current = self._backend._visible(self._name, id)
            if current is None or current.get(field) != expect:
                return None
            merged = copy_record(current)
            merged.update(update)
            merged["id"] = id
            if self._backend._tx_depth:
                self._backend._stage(self._name, id, merged)
            else:
                self._backend._write_disk(self._name, id, merged)
            return copy_record(merged)
