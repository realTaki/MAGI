"""BaseBook — 数据簿基类，自动映射 ORM → dataclass。

子类提供 model_cls / dto_cls 两个类属性即可。
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from typing import ClassVar, Protocol, TypeVar

from sqlalchemy import DateTime, Integer
from sqlalchemy.orm import Mapped, mapped_column

from magi.bus.db.base import Base, utcnow_naive
from magi.bus.db.engine import EngineFactory

RowT = TypeVar("RowT", bound=Base)


class _Dataclass(Protocol):
    """Structural Protocol for ``@dataclasses.dataclass``-decorated classes.

    ``dataclasses.fields()`` types its argument as
    ``DataclassInstance | type[DataclassInstance]``; without this
    bound, Pylance sees ``type[DtoT]`` (a TypeVar with no upper
    bound) as ``type[Any]`` and rejects the call. Binding DtoT to
    this Protocol makes the class satisfy the dataclass shape
    structurally without forcing every subclass to inherit from a
    concrete base.
    """

    __dataclass_fields__: ClassVar[dict]


DtoT = TypeVar("DtoT", bound=_Dataclass)


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class BaseRecord:
    """Common JSON-safe fields for every persisted library DTO.

    ``id`` is the database-local identity.  Time values deliberately remain
    ``datetime`` throughout the database, Book and API layers; presentation
    formatting belongs to the frontend.
    """

    id: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None


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


class BaseBook[RowT: Base, DtoT: _Dataclass]:
    """子类设置 model_cls / dto_cls，自动处理 Session 和映射。"""

    model_cls: type[RowT]
    dto_cls: type[DtoT]

    def __init__(self, factory: EngineFactory):
        self._factory = factory

    def _session(self):
        return self._factory.session()

    def _row_to_dto(self, row: RowT) -> DtoT:
        kwargs: dict = {}
        for f in dataclasses.fields(self.dto_cls):
            if hasattr(row, f.name):
                val = getattr(row, f.name)
                kwargs[f.name] = val
        return self.dto_cls(**kwargs)
