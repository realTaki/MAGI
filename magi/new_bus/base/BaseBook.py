"""BaseRecord, ORM mixin, then BaseBook."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, fields, replace
from typing import Any, Self, get_args, get_origin, get_type_hints

from sqlalchemy import DateTime, Integer, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from .engine import EngineFactory
from .errors import InvalidJobError
from .time import BaseTime, load_dt, utcnow


@dataclass(kw_only=True)
class BaseRecord:
    """id / created_at / updated_at. BUS assigns these."""

    id: int = 0
    created_at: BaseTime | None = None
    updated_at: BaseTime | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def _type_hints(cls) -> dict[str, Any]:
        localns: dict[str, Any] = dict(vars(cls))
        for klass in cls.__mro__:
            for base in getattr(klass, "__orig_bases__", ()):
                origin = get_origin(base)
                if origin is None:
                    continue
                for param, arg in zip(getattr(origin, "__type_params__", ()), get_args(base)):
                    localns[getattr(param, "__name__", str(param))] = arg
        return get_type_hints(cls, localns=localns)

    @classmethod
    def parse(cls, data: Mapping[str, Any]) -> Self:
        hints = cls._type_hints()
        return cls(
            **{
                key: load_dt(hints[key], value)
                for key, value in data.items()
                if key in hints
            }
        )

    @classmethod
    def from_row(cls, row: BaseRecordMixin) -> Self:
        return cls.parse({item.name: getattr(row, item.name) for item in fields(cls)})


class BaseRecordMixin(DeclarativeBase):
    """Shared ORM columns for every Book / Job table."""

    __abstract__ = True

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[BaseTime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[BaseTime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


class BaseBook[RecordT: BaseRecord]:
    """Internal record collection. Only OpenBookJobBoard may call these methods.

    Firmware Books set ``record_cls`` and ``row_cls``. CRUD goes through the Row.
    """

    record_cls: type[RecordT]
    row_cls: type[BaseRecordMixin]

    def __init__(self, factory: EngineFactory) -> None:
        cls = type(self)
        if getattr(cls, "row_cls", None) is None:
            raise InvalidJobError(f"{cls.__name__} must set row_cls")
        self._factory = factory

    def _session(self):
        return self._factory.session()

    def _validate_add(self, record: RecordT) -> None:
        """Validate a record before it is persisted.

        Subclasses own domain invariants and override this hook where needed.
        They must not open or commit a separate transaction.
        """

    def add(self, record: RecordT) -> int:
        now = utcnow()
        prepared = replace(
            record,
            created_at=record.created_at or now,
            updated_at=now,
        )
        self._validate_add(prepared)
        with self._session() as session:
            values = prepared.to_dict()
            values.pop("id", None)
            row = type(self).row_cls(**values)
            session.add(row)
            session.commit()
            return int(row.id)

    def get(self, record_id: int) -> RecordT | None:
        with self._session() as session:
            row = session.get(type(self).row_cls, record_id)
            return None if row is None else type(self).record_cls.from_row(row)

    def exists(self, record_id: int) -> bool:
        with self._session() as session:
            return session.get(type(self).row_cls, record_id) is not None

    def update(self, record: RecordT) -> bool:
        self._validate_add(record)
        with self._session() as session:
            row = session.get(type(self).row_cls, record.id)
            if row is None:
                return False
            stored = replace(
                record, created_at=row.created_at, updated_at=utcnow()
            )
            values = stored.to_dict()
            values.pop("id", None)
            for key, value in values.items():
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

    def list(self, **filters: object) -> list[RecordT]:
        row_cls = type(self).row_cls
        stmt = select(row_cls).order_by(row_cls.id)
        if filters:
            stmt = stmt.filter_by(**filters)
        with self._session() as session:
            return [type(self).record_cls.from_row(row) for row in session.scalars(stmt)]
