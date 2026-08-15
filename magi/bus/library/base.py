"""BaseBook — 数据簿基类，自动映射 ORM → dataclass。

子类提供 model_cls / dto_cls 两个类属性即可。
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime

from sqlalchemy import DateTime, Integer
from sqlalchemy.orm import Mapped, mapped_column

from magi.bus.db.base import Base, utcnow_naive
from magi.bus.db.engine import EngineFactory

@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class BaseRecord:
    """Common JSON-safe fields for every persisted library DTO.

    ``id`` is database-owned local identity. Callers cannot supply it when
    constructing a DTO; Books fill it from a persisted row. Time values
    deliberately remain ``datetime`` throughout the database, Book and API
    layers; presentation formatting belongs to the frontend.
    """

    id: int = dataclasses.field(default=0, init=False)
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def to_dict(self) -> dict:
        """Return the DTO's transport-ready field mapping.

        Values deliberately retain their native types, including ``datetime``;
        the API transport is responsible for JSON encoding and the frontend
        for presentation formatting.  A record with a genuinely different
        public projection may override this method locally.
        """

        return dataclasses.asdict(self)


class BaseRecordMixin(Base):
    """The single ORM record shape shared by all library tables."""

    __abstract__ = True

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, onupdate=utcnow_naive, nullable=False
    )


def parse_iso_utc_naive(value: str) -> datetime:
    """Parse an external ISO-8601 timestamp into naive UTC for ORM storage."""

    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed
    return parsed.astimezone(UTC).replace(tzinfo=None)


class BaseBook[RowT: BaseRecordMixin, DtoT: BaseRecord]:
    """子类设置 model_cls / dto_cls，自动处理 Session 和映射。"""

    model_cls: type[RowT]
    dto_cls: type[DtoT]

    def __init__(self, factory: EngineFactory):
        self._factory = factory

    def _session(self):
        return self._factory.session()

    def _row_to_dto(self, row: RowT) -> DtoT:
        init_kwargs: dict = {}
        database_values: dict = {}
        for f in dataclasses.fields(self.dto_cls):
            if hasattr(row, f.name):
                val = getattr(row, f.name)
                if f.init:
                    init_kwargs[f.name] = val
                else:
                    database_values[f.name] = val
        dto = self.dto_cls(**init_kwargs)
        for name, value in database_values.items():
            object.__setattr__(dto, name, value)
        return dto
