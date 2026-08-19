"""Shared SQL record store used by SQLite and PostgreSQL.

Each Book and each JobBoard is its own table. Job tables keep a JSON
``data`` column. Book tables use one column per record field.
"""

from __future__ import annotations

import json
import re
import threading
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any, Protocol

from sqlalchemy.orm import sessionmaker

from ._common import check_collection, coerce_id, copy_record, matches, sort_records
from .backend import DatabaseBackend, RecordStore
from .errors import BackendError

_IDENT = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")



class SqlDriver(Protocol):
    placeholder: str
    id_column: str

    def connect(self) -> Any: ...


class SqlBackend(DatabaseBackend):
    def __init__(self, driver: SqlDriver, engine=None) -> None:
        self._driver = driver
        self.engine = engine
        self._sessions = sessionmaker(bind=engine, expire_on_commit=False) if engine else None
        self._lock = threading.RLock()
        self._tx_depth = 0
        try:
            self._conn = driver.connect()
        except Exception as exc:
            raise BackendError("failed to open SQL backend") from exc
        self.ensure()

    @contextmanager
    def session(self) -> Iterator[Any]:
        if self._sessions is None:
            raise BackendError("SQL backend has no engine")
        item = self._sessions()
        try:
            yield item
        finally:
            item.close()

    def ensure(self) -> None:
        return

    def records(self, name: str, **_spec: Any) -> RecordStore:
        table = table_name(check_collection(name))
        with self._lock:
            fields = self._fields_from_table(table)
            if fields is None and not self._has_table(table):
                self._ensure_blob_table(table)
        return _SqlStore(self, table, fields)

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

    def _has_table(self, table: str) -> bool:
        engine = self.engine
        if engine is None:
            return False
        from sqlalchemy import inspect

        return inspect(engine).has_table(table)

    def _fields_from_table(self, table: str) -> tuple[tuple[str, str], ...] | None:
        engine = self.engine
        if engine is None or not self._has_table(table):
            return None
        from sqlalchemy import Boolean, DateTime, Integer, inspect

        columns = inspect(engine).get_columns(table)
        names = [column["name"] for column in columns]
        if "data" in names:
            return None
        kinds: list[tuple[str, str]] = []
        for column in columns:
            sql_type = column["type"]
            if isinstance(sql_type, Boolean):
                kind = "bool"
            elif isinstance(sql_type, DateTime):
                kind = "datetime"
            elif isinstance(sql_type, Integer):
                kind = "int"
            else:
                kind = "str"
            kinds.append((column["name"], kind))
        return tuple(kinds)

    def _ensure_blob_table(self, table: str) -> None:
        columns = [
            self._driver.id_column,
            "status TEXT",
            "created_at TEXT",
            "data TEXT NOT NULL",
        ]
        try:
            self._execute(f"CREATE TABLE IF NOT EXISTS {table} ({', '.join(columns)})")
            self._execute(
                f"CREATE INDEX IF NOT EXISTS ix_{table}_claim ON {table} (status, created_at)"
            )
            self._autocommit()
        except Exception as exc:
            raise BackendError(f"failed to prepare table {table}") from exc


class _SqlStore(RecordStore):
    def __init__(
        self,
        backend: SqlBackend,
        table: str,
        fields: tuple[tuple[str, str], ...] | None,
    ) -> None:
        self._backend = backend
        self._table = table
        self._fields = fields
        self._kinds = dict(fields) if fields else {}

    def insert(self, record: Mapping[str, Any]) -> dict[str, Any]:
        data = copy_record(record)
        with self._backend._lock:
            record_id = coerce_id(data.get("id"))
            try:
                if record_id is None:
                    data["id"] = self._insert_new(data)
                elif self._fields is None:
                    self._backend._execute(
                        f"INSERT INTO {self._table} (id, status, created_at, data) "
                        "VALUES (?, ?, ?, ?)",
                        (record_id, data.get("status"), data.get("created_at"), json.dumps(data)),
                    )
                else:
                    names, values = self._typed_row(data)
                    placeholders = ", ".join("?" for _ in names)
                    self._backend._execute(
                        f"INSERT INTO {self._table} ({', '.join(names)}) VALUES ({placeholders})",
                        values,
                    )
            except Exception as exc:
                raise BackendError(f"duplicate id {data.get('id')}") from exc
            self._backend._autocommit()
            return copy_record(data)

    def get(self, id: int) -> dict[str, Any] | None:
        with self._backend._lock:
            if self._fields is None:
                row = self._backend._execute(
                    f"SELECT data FROM {self._table} WHERE id = ?", (id,)
                ).fetchone()
                if row is None:
                    return None
                return json.loads(_cell(row, "data", 0))
            row = self._backend._execute(
                f"SELECT * FROM {self._table} WHERE id = ?", (id,)
            ).fetchone()
        return None if row is None else self._from_row(row)

    def replace(self, id: int, record: Mapping[str, Any]) -> dict[str, Any]:
        data = copy_record(record)
        data["id"] = id
        with self._backend._lock:
            if self._fields is None:
                cursor = self._backend._execute(
                    f"UPDATE {self._table} SET status = ?, created_at = ?, data = ? WHERE id = ?",
                    (data.get("status"), data.get("created_at"), json.dumps(data), id),
                )
            else:
                names, values = self._typed_row(data)
                assignments = ", ".join(f"{name} = ?" for name in names if name != "id")
                params = [value for name, value in zip(names, values, strict=True) if name != "id"]
                params.append(id)
                cursor = self._backend._execute(
                    f"UPDATE {self._table} SET {assignments} WHERE id = ?",
                    params,
                )
            if _rowcount(cursor) == 0:
                raise BackendError(f"missing id {id}")
            self._backend._autocommit()
            return copy_record(data)

    def delete(self, id: int) -> bool:
        with self._backend._lock:
            cursor = self._backend._execute(f"DELETE FROM {self._table} WHERE id = ?", (id,))
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
            if self._fields is None:
                if status is None:
                    rows = self._backend._execute(f"SELECT data FROM {self._table}").fetchall()
                else:
                    rows = self._backend._execute(
                        f"SELECT data FROM {self._table} WHERE status = ?", (status,)
                    ).fetchall()
                records = [json.loads(_cell(row, "data", 0)) for row in rows]
            else:
                clauses: list[str] = []
                params: list[Any] = []
                if status is not None and "status" in self._kinds:
                    clauses.append("status = ?")
                    params.append(status)
                for key, value in (eq or {}).items():
                    if key in self._kinds or key == "id":
                        clauses.append(f"{key} = ?")
                        params.append(_encode(self._kinds.get(key, "int"), value))
                where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
                rows = self._backend._execute(
                    f"SELECT * FROM {self._table}{where}", params
                ).fetchall()
                records = [self._from_row(row) for row in rows]
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
            current = self.get(id)
            if current is None or current.get(field) != expect:
                return None
            merged = copy_record(current)
            merged.update(update)
            merged["id"] = id
            try:
                return self.replace(id, merged)
            except BackendError:
                return None

    def _insert_new(self, data: dict[str, Any]) -> int:
        returning = self._backend._driver.placeholder == "%s"
        if self._fields is None:
            sql = f"INSERT INTO {self._table} (status, created_at, data) VALUES (?, ?, ?)"
            params: list[Any] = [data.get("status"), data.get("created_at"), json.dumps(data)]
        else:
            names = [name for name, _kind in self._fields if name != "id"]
            params = [_encode(self._kinds[name], data.get(name)) for name in names]
            placeholders = ", ".join("?" for _ in names)
            sql = f"INSERT INTO {self._table} ({', '.join(names)}) VALUES ({placeholders})"
        if returning:
            sql += " RETURNING id"
        cursor = self._backend._execute(sql, params)
        if returning:
            row = cursor.fetchone()
            if row is None:
                raise BackendError("insert did not return id")
            new_id = int(_cell(row, "id", 0))
        else:
            new_id = int(cursor.lastrowid)
        data["id"] = new_id
        if self._fields is None:
            self._backend._execute(
                f"UPDATE {self._table} SET data = ? WHERE id = ?",
                (json.dumps(data), new_id),
            )
        return new_id

    def _typed_row(self, data: Mapping[str, Any]) -> tuple[list[str], list[Any]]:
        names = [name for name, _kind in self._fields or ()]
        values = [_encode(self._kinds[name], data.get(name)) for name in names]
        return names, values

    def _from_row(self, row: Any) -> dict[str, Any]:
        if isinstance(row, Mapping):
            mapping = row
        else:
            names = [name for name, _kind in self._fields or ()]
            mapping = dict(zip(names, row, strict=False))
        return {name: _decode(kind, mapping.get(name)) for name, kind in (self._fields or ())}


def table_name(collection: str) -> str:
    name = collection.replace(".", "_")
    if not _IDENT.fullmatch(name):
        raise BackendError(f"invalid table name {name!r}")
    return name


def _encode(kind: str, value: Any) -> Any:
    if value is None:
        return None
    if kind == "bool":
        return int(bool(value))
    if kind == "json":
        return json.dumps(value)
    return value


def _decode(kind: str, value: Any) -> Any:
    if value is None:
        return None
    if kind == "bool":
        return bool(value)
    if kind == "json":
        return json.loads(value) if isinstance(value, str) else value
    return value


def _cell(row: Any, key: str | int, index: int) -> Any:
    if isinstance(row, Mapping):
        return row[key] if key in row else row[index]
    return row[index]


def _rowcount(cursor: Any) -> int:
    count = getattr(cursor, "rowcount", None)
    return int(count) if count is not None and count >= 0 else 0
