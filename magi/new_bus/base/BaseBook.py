"""BaseRecord, ORM mixin, then BaseBook."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields, replace
from datetime import datetime
from typing import Any, ClassVar, Self, get_type_hints

from sqlalchemy import DateTime, Integer, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from .engine import EngineFactory
from .errors import InvalidJobError
from .time import dump_dt, load_dt, utcnow


@dataclass(kw_only=True)
class BaseRecord:
    """id / created_at / updated_at. BUS assigns these."""

    id: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {item.name: dump_dt(getattr(self, item.name)) for item in fields(self)}

    @classmethod
    def parse(cls, data: Mapping[str, Any]) -> Self:
        hints = get_type_hints(cls)
        allowed = {item.name for item in fields(cls)}
        return cls(
            **{
                key: load_dt(hints.get(key), value)
                for key, value in data.items()
                if key in allowed
            }
        )

    def merge(self, changes: Mapping[str, Any]) -> Self:
        hints = get_type_hints(type(self))
        allowed = {item.name for item in fields(type(self))}
        updates = {
            key: load_dt(hints.get(key), value)
            for key, value in changes.items()
            if key in allowed
        }
        return replace(self, **updates)


class BaseRecordMixin(DeclarativeBase):
    """Shared ORM columns for every Book / Job table."""

    __abstract__ = True

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


class BaseBook:
    """Internal record collection. Only ManageBookJobBoard may call these methods.

    Firmware Books set ``record_cls`` and ``row_cls``. CRUD goes through the Row.
    """

    record_cls: ClassVar[type[BaseRecord]] = BaseRecord
    row_cls: ClassVar[type[BaseRecordMixin]]

    def __init__(self, factory: EngineFactory) -> None:
        cls = type(self)
        if getattr(cls, "row_cls", None) is None:
            raise InvalidJobError(f"{cls.__name__} must set row_cls")
        self._factory = factory

    def _session(self):
        return self._factory.session()

    def add(self, record: BaseRecord) -> int:
        now = utcnow()
        prepared = replace(
            record,
            id=0,
            created_at=record.created_at or now,
            updated_at=now,
        )
        with self._session() as session:
            row = type(self).row_cls(**self._row_values(prepared))
            session.add(row)
            session.commit()
            return int(row.id)

    def get(self, record_id: int) -> BaseRecord | None:
        with self._session() as session:
            row = session.get(type(self).row_cls, record_id)
            return None if row is None else self._from_row(row)

    def exists(self, record_id: int) -> bool:
        with self._session() as session:
            return session.get(type(self).row_cls, record_id) is not None

    def update(self, record: BaseRecord) -> bool:
        with self._session() as session:
            row = session.get(type(self).row_cls, record.id)
            if row is None:
                return False
            stored = replace(
                record, id=row.id, created_at=row.created_at, updated_at=utcnow()
            )
            for key, value in self._row_values(stored).items():
                setattr(row, key, value)
            session.commit()
            return True

    def delete(self, record_id: int) -> bool:
        with self._session() as session:
            row = session.get(type(self).row_cls, record_id)
            if row is None:
                return False
            session.delete(row)
            session.commit()
            return True

    def list(self, **filters: object) -> list[BaseRecord]:
        row_cls = type(self).row_cls
        stmt = select(row_cls).order_by(row_cls.id)
        if filters:
            stmt = stmt.filter_by(**filters)
        with self._session() as session:
            return [self._from_row(row) for row in session.scalars(stmt)]

    def _book(self) -> str:
        return type(self).record_cls.__name__

    def _row_values(self, record: BaseRecord) -> dict[str, Any]:
        return {item.name: getattr(record, item.name) for item in fields(record) if item.name != "id"}

    def _from_row(self, row: BaseRecordMixin) -> BaseRecord:
        return self.record_cls.parse(
            {item.name: getattr(row, item.name) for item in fields(self.record_cls)}
        )
