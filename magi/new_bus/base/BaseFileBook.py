"""BaseFileBook — a BaseBook that must live on FileBackend.

Regular Books store records through whatever Backend the Bus opened.
File Books always sit on disk as one JSON file per record, even when
the Bus primary backend is SQLite or PostgreSQL.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any, ClassVar, cast

from .backends.file import FileBackend, _FileStore
from .BaseBook import BaseBook
from .BaseRecord import BaseRecord
from .errors import BookNotFoundError, InvalidJobError
from .time import utcnow


class BaseFileBook(BaseBook):
    """File-backed Book. ``backend`` must be a :class:`FileBackend`."""

    __tablename__: ClassVar[str] = ""

    def __init__(self, backend) -> None:
        cls = type(self)
        if not cls.name:
            raise InvalidJobError(f"{cls.__name__} must set class variable name")
        if not cls.__tablename__:
            raise InvalidJobError(f"{cls.__name__} must set __tablename__")
        self._require_backend(backend)
        self._backend = backend
        self._store = backend.records(cls.__tablename__)

    def _require_backend(self, backend) -> None:
        if not isinstance(backend, FileBackend):
            raise InvalidJobError("BaseFileBook requires FileBackend")

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

    @property
    def directory(self) -> Path:
        return cast(_FileStore, self._store).directory

    def path_for(self, record_id: int) -> Path:
        return cast(_FileStore, self._store).path_for(record_id)
