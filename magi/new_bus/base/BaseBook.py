"""BaseBook is BUS-internal current state. External modules never hold this object."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import fields, replace
from typing import Any, ClassVar

from sqlalchemy import select

from .backends.backend import DatabaseBackend
from .BaseRecord import BaseRecord, BaseRecordMixin
from .errors import BookNotFoundError, InvalidJobError
from .time import utcnow


class BaseBook:
    """Internal record collection. Only ManageBookJobBoard may call these methods.

    Firmware Books set ``record_cls`` and ``row_cls``. CRUD goes through the Row.
    """

    name: ClassVar[str] = ""
    record_cls: ClassVar[type[BaseRecord]] = BaseRecord
    row_cls: ClassVar[type[BaseRecordMixin] | None] = None

    def __init__(self, backend) -> None:
        cls = type(self)
        if not cls.name:
            raise InvalidJobError(f"{cls.__name__} must set class variable name")
        if cls.row_cls is None:
            raise InvalidJobError(f"{cls.__name__} must set row_cls")
        self._require_backend(backend)
        self._backend = backend

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
        with self._backend.session() as session:
            row = type(self).row_cls(**self._row_values(prepared))
            session.add(row)
            session.commit()
            return int(row.id)

    def get(self, record_id: int) -> BaseRecord | None:
        with self._backend.session() as session:
            row = session.get(type(self).row_cls, record_id)
            return None if row is None else self._from_row(row)

    def update(self, record: BaseRecord) -> int:
        if not record.id:
            raise InvalidJobError("update requires record.id")
        with self._backend.session() as session:
            row = session.get(type(self).row_cls, record.id)
            if row is None:
                raise BookNotFoundError(f"book {self.name!r} has no id {record.id}")
            stored = replace(
                record, id=row.id, created_at=row.created_at, updated_at=utcnow()
            )
            for key, value in self._row_values(stored).items():
                setattr(row, key, value)
            session.commit()
            return int(row.id)

    def delete(self, record_id: int) -> bool:
        with self._backend.session() as session:
            row = session.get(type(self).row_cls, record_id)
            if row is None:
                return False
            session.delete(row)
            session.commit()
            return True

    def list(self, filters: Mapping[str, Any] | None = None) -> list[BaseRecord]:
        row_cls = type(self).row_cls
        stmt = select(row_cls).order_by(row_cls.id)
        if filters:
            stmt = stmt.filter_by(**filters)
        with self._backend.session() as session:
            return [self._from_row(row) for row in session.scalars(stmt)]

    def _row_values(self, record: BaseRecord) -> dict[str, Any]:
        return {item.name: getattr(record, item.name) for item in fields(record) if item.name != "id"}

    def _from_row(self, row: BaseRecordMixin) -> BaseRecord:
        return self.record_cls.parse(
            {item.name: getattr(row, item.name) for item in fields(self.record_cls)}
        )
