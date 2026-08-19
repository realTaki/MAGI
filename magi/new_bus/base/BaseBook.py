"""BaseBook is BUS-internal current state. External modules never hold this object."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any, ClassVar

from .backends.backend import DatabaseBackend
from .BaseRecord import BaseRecord
from .errors import BookNotFoundError, InvalidJobError
from .time import utcnow


class BaseBook:
    """Internal record collection. Only ManageBookJobBoard may call these methods.

    Firmware Books set ``record_cls`` to the dataclass that lists their fields.
    """

    name: ClassVar[str] = ""
    record_cls: ClassVar[type[BaseRecord]] = BaseRecord

    def __init__(self, backend) -> None:
        if not type(self).name:
            raise InvalidJobError(f"{type(self).__name__} must set class variable name")
        self._require_backend(backend)
        cls = type(self)
        self._store = backend.records(f"books.{cls.name}")

    def _require_backend(self, backend) -> None:
        if not isinstance(backend, DatabaseBackend):
            raise InvalidJobError("BaseBook requires a database backend")

    def add(self, record: BaseRecord) -> int:
        now = utcnow()
        prepared = replace(
            record,
            id=0,
            created_at=record.created_at or now,
            updated_at=now,
        )
        stored = self._store.insert(prepared.to_dict())
        return int(stored["id"])

    def get(self, record_id: int) -> BaseRecord | None:
        data = self._store.get(record_id)
        return None if data is None else self.record_cls.parse(data)

    def update(self, record: BaseRecord) -> int:
        if not record.id:
            raise InvalidJobError("update requires record.id")
        current = self.get(record.id)
        if current is None:
            raise BookNotFoundError(f"book {self.name!r} has no id {record.id}")
        stored = replace(
            record, id=current.id, created_at=current.created_at, updated_at=utcnow()
        )
        self._store.replace(stored.id, stored.to_dict())
        return stored.id

    def delete(self, record_id: int) -> bool:
        return self._store.delete(record_id)

    def list(self, filters: Mapping[str, Any] | None = None) -> list[BaseRecord]:
        return [self.record_cls.parse(row) for row in self._store.find(eq=filters)]
