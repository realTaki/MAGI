"""Book is BUS-internal current state. External modules never hold this object."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, ClassVar
from uuid import uuid4

from ..errors import BookNotFoundError, InvalidJobError
from .backend import Backend, RecordStore


def utcnow() -> datetime:
    return datetime.now(UTC)


class Book:
    """Internal record collection. Only ManageBookJobBoard may call these methods.

    Firmware Books set ``record_cls`` to the dataclass that lists their fields.
    """

    record_cls: ClassVar[type | None] = None

    def __init__(self, name: str, backend: Backend) -> None:
        if not name:
            raise InvalidJobError("book name is required")
        self.name = name
        self._store: RecordStore = backend.records(f"books.{name}")

    def _validate_write(self, record: Mapping[str, Any]) -> None:
        """Firmware Books override this to enforce their record protocol."""

    def insert(self, record: Mapping[str, Any]) -> dict[str, Any]:
        data = dict(record)
        record_id = str(data.get("id") or uuid4().hex)
        if self._store.get(record_id) is not None:
            raise InvalidJobError(f"book {self.name!r} already has id {record_id}")
        now = utcnow().isoformat()
        data["id"] = record_id
        data.setdefault("created_at", now)
        data["updated_at"] = now
        self._validate_write(data)
        return self._store.insert(data)

    def get(self, id: str) -> dict[str, Any] | None:
        return self._store.get(id)

    def require(self, id: str) -> dict[str, Any]:
        record = self._store.get(id)
        if record is None:
            raise BookNotFoundError(f"book {self.name!r} has no id {id}")
        return record

    def update(self, id: str, changes: Mapping[str, Any]) -> dict[str, Any]:
        current = self.require(id)
        merged = dict(current)
        for key, value in changes.items():
            if key in {"id", "created_at"}:
                continue
            merged[key] = value
        merged["id"] = id
        merged["updated_at"] = utcnow().isoformat()
        self._validate_write(merged)
        return self._store.replace(id, merged)

    def delete(self, id: str) -> None:
        self.require(id)
        self._store.delete(id)

    def query(self, filters: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
        return self._store.find(eq=filters)
