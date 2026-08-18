"""BaseBook is BUS-internal current state. External modules never hold this object."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields, replace
from datetime import UTC, datetime
from typing import Any, ClassVar, Self

from .backends import Backend, RecordStore
from .errors import BookNotFoundError, InvalidJobError


def utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(kw_only=True)
class BaseRecord:
    """Fields every BaseBook row has. BUS assigns these."""

    id: int = 0
    created_at: str | None = None
    updated_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {item.name: getattr(self, item.name) for item in fields(self)}

    @classmethod
    def parse(cls, data: Mapping[str, Any]) -> Self:
        """Build a record from a mapping, keeping only declared fields."""
        allowed = {item.name for item in fields(cls)}
        kwargs = {key: value for key, value in data.items() if key in allowed}
        return cls(**kwargs)

    def merge(self, changes: Mapping[str, Any]) -> Self:
        """Apply declared, non-owned fields from ``changes`` onto this record."""
        allowed = {item.name for item in fields(type(self))} - (OWNED_FIELDS - {"updated_at"})
        updates = {key: value for key, value in changes.items() if key in allowed}
        return replace(self, **updates)


OWNED_FIELDS = frozenset(item.name for item in fields(BaseRecord))


class BaseBook:
    """Internal record collection. Only ManageBookJobBoard may call these methods.

    Firmware Books set ``record_cls`` to the dataclass that lists their fields.
    """

    record_cls: ClassVar[type[BaseRecord]] = BaseRecord

    def __init__(self, name: str, backend: Backend) -> None:
        if not name:
            raise InvalidJobError("book name is required")
        self.name = name
        self._store: RecordStore = backend.records(f"books.{name}")

    def _validate_write(self, record: BaseRecord) -> None:
        """Firmware Books override this to enforce their record protocol."""

    def add(self, record: BaseRecord) -> BaseRecord:
        if record.id and self._store.get(record.id) is not None:
            raise InvalidJobError(f"book {self.name!r} already has id {record.id}")
        now = utcnow().isoformat()
        prepared = replace(
            record,
            created_at=record.created_at or now,
            updated_at=now,
        )
        self._validate_write(prepared)
        return self.record_cls.parse(self._store.insert(prepared.to_dict()))

    def get(self, id: int) -> BaseRecord | None:
        data = self._store.get(id)
        return None if data is None else self.record_cls.parse(data)

    def require(self, id: int) -> bool:
        return self.get(id) is not None

    def update(self, record: BaseRecord) -> BaseRecord:
        if not record.id:
            raise InvalidJobError("update requires record.id")
        current = self.get(record.id)
        if current is None:
            raise BookNotFoundError(f"book {self.name!r} has no id {record.id}")
        stored = replace(
            record, id=current.id, created_at=current.created_at, updated_at=utcnow().isoformat()
        )
        self._validate_write(stored)
        self._store.replace(stored.id, stored.to_dict())
        return stored

    def delete(self, id: int) -> None:
        if not self.require(id):
            raise BookNotFoundError(f"book {self.name!r} has no id {id}")
        self._store.delete(id)

    def query(self, filters: Mapping[str, Any] | None = None) -> list[BaseRecord]:
        return [self.record_cls.parse(row) for row in self._store.find(eq=filters)]
