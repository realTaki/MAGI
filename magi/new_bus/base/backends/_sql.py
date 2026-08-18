"""Shared SQL record store used by SQLite and PostgreSQL."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any, Protocol

from ..errors import BackendError
from ._common import check_collection, coerce_id, copy_record, matches, sort_records
from .backend import Backend, RecordStore

_SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
    collection TEXT NOT NULL,
    id INTEGER NOT NULL,
    status TEXT,
    created_at TEXT,
    data TEXT NOT NULL,
    PRIMARY KEY (collection, id)
)
"""
_INDEX = """
CREATE INDEX IF NOT EXISTS ix_records_claim
ON records (collection, status, created_at)
"""


class SqlDriver(Protocol):
    placeholder: str

    def connect(self) -> Any: ...


class SqlBackend(Backend):
    def __init__(self, driver: SqlDriver) -> None:
        self._driver = driver
        self._lock = threading.RLock()
        self._tx_depth = 0
        try:
            self._conn = driver.connect()
        except Exception as exc:
            raise BackendError("failed to open SQL backend") from exc
        self.ensure()

    def ensure(self) -> None:
        try:
            self._execute(_SCHEMA)
            self._execute(_INDEX)
            self._conn.commit()
        except Exception as exc:
            raise BackendError("failed to prepare SQL backend") from exc

    def records(self, name: str) -> RecordStore:
        return _SqlStore(self, check_collection(name))

    @contextmanager
    def transaction(self) -> Iterator[None]:
        with self._lock:
            if self._tx_depth == 0:
                self._begin()
            self._tx_depth += 1
            try:
                yield
                if self._tx_depth == 1:
                    self._conn.commit()
            except Exception:
                if self._tx_depth == 1:
                    self._conn.rollback()
                raise
            finally:
                self._tx_depth -= 1

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception as exc:
                raise BackendError("failed to close SQL backend") from exc

    def _sql(self, statement: str) -> str:
        if self._driver.placeholder == "?":
            return statement
        return statement.replace("?", self._driver.placeholder)

    def _execute(self, statement: str, params: Sequence[Any] = ()) -> Any:
        return self._conn.execute(self._sql(statement), tuple(params))

    def _begin(self) -> None:
        try:
            self._conn.execute("BEGIN")
        except Exception as exc:
            raise BackendError("failed to begin transaction") from exc

    def _autocommit(self) -> None:
        if self._tx_depth == 0:
            self._conn.commit()


class _SqlStore(RecordStore):
    def __init__(self, backend: SqlBackend, name: str) -> None:
        self._backend = backend
        self._name = name

    def insert(self, record: Mapping[str, Any]) -> dict[str, Any]:
        data = copy_record(record)
        with self._backend._lock:
            record_id = coerce_id(data.get("id"))
            if record_id is None:
                row = self._backend._execute(
                    "SELECT COALESCE(MAX(id), 0) + 1 FROM records WHERE collection = ?",
                    (self._name,),
                ).fetchone()
                record_id = int(
                    row[0] if not isinstance(row, Mapping) else next(iter(row.values()))
                )
                data["id"] = record_id
            try:
                self._backend._execute(
                    "INSERT INTO records (collection, id, status, created_at, data) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        self._name,
                        record_id,
                        data.get("status"),
                        data.get("created_at"),
                        json.dumps(data),
                    ),
                )
            except Exception as exc:
                raise BackendError(f"duplicate id {record_id}") from exc
            self._backend._autocommit()
            return copy_record(data)

    def get(self, id: int) -> dict[str, Any] | None:
        with self._backend._lock:
            row = self._backend._execute(
                "SELECT data FROM records WHERE collection = ? AND id = ?",
                (self._name, id),
            ).fetchone()
        if row is None:
            return None
        return json.loads(_row_data(row))

    def replace(self, id: int, record: Mapping[str, Any]) -> dict[str, Any]:
        data = copy_record(record)
        data["id"] = id
        with self._backend._lock:
            cursor = self._backend._execute(
                "UPDATE records SET status = ?, created_at = ?, data = ? "
                "WHERE collection = ? AND id = ?",
                (data.get("status"), data.get("created_at"), json.dumps(data), self._name, id),
            )
            if _rowcount(cursor) == 0:
                raise BackendError(f"missing id {id}")
            self._backend._autocommit()
            return copy_record(data)

    def delete(self, id: int) -> bool:
        with self._backend._lock:
            cursor = self._backend._execute(
                "DELETE FROM records WHERE collection = ? AND id = ?",
                (self._name, id),
            )
            deleted = _rowcount(cursor) > 0
            self._backend._autocommit()
            return deleted

    def find(
        self,
        *,
        status: str | None = None,
        eq: Mapping[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        with self._backend._lock:
            if status is None:
                rows = self._backend._execute(
                    "SELECT data FROM records WHERE collection = ?",
                    (self._name,),
                ).fetchall()
            else:
                rows = self._backend._execute(
                    "SELECT data FROM records WHERE collection = ? AND status = ?",
                    (self._name, status),
                ).fetchall()
        records = [json.loads(_row_data(row)) for row in rows]
        if eq:
            records = [record for record in records if matches(record, eq)]
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
            row = self._backend._execute(
                "SELECT data FROM records WHERE collection = ? AND id = ?",
                (self._name, id),
            ).fetchone()
            if row is None:
                return None
            current = json.loads(_row_data(row))
            if current.get(field) != expect:
                return None
            merged = copy_record(current)
            merged.update(update)
            merged["id"] = id
            cursor = self._backend._execute(
                "UPDATE records SET status = ?, created_at = ?, data = ? "
                "WHERE collection = ? AND id = ?",
                (
                    merged.get("status"),
                    merged.get("created_at"),
                    json.dumps(merged),
                    self._name,
                    id,
                ),
            )
            if _rowcount(cursor) == 0:
                return None
            self._backend._autocommit()
            return copy_record(merged)


def _row_data(row: Any) -> str:
    if isinstance(row, Mapping):
        return str(row["data"])
    return str(row[0])


def _rowcount(cursor: Any) -> int:
    count = getattr(cursor, "rowcount", None)
    return int(count) if count is not None and count >= 0 else 0
