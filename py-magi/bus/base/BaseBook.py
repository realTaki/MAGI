"""BaseRecord, ORM mixin, then BaseBook."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, fields, is_dataclass, replace
from enum import Enum
from types import UnionType
from typing import Any, Self, Union, get_args, get_origin, get_type_hints

from sqlalchemy import DateTime, Integer, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from .engine import EngineFactory
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
                for param, arg in zip(
                    getattr(origin, "__type_params__", ()), get_args(base), strict=False
                ):
                    localns[getattr(param, "__name__", str(param))] = arg
        return get_type_hints(cls, localns=localns)

    @classmethod
    def _parse_value(cls, annotation: Any, value: Any) -> Any:
        """Restore a JSON-compatible value to *annotation*'s runtime shape."""
        if value is None or annotation is Any:
            return value
        origin = get_origin(annotation)
        args = get_args(annotation)
        if origin in {Union, UnionType}:
            for candidate in args:
                if candidate is type(None):
                    continue
                if isinstance(candidate, type) and isinstance(value, candidate):
                    return value
            for candidate in args:
                if candidate is type(None):
                    continue
                try:
                    return cls._parse_value(candidate, value)
                except (TypeError, ValueError):
                    continue
            return value
        if origin is list and isinstance(value, list):
            item_type = args[0] if args else Any
            return [cls._parse_value(item_type, item) for item in value]
        if origin is set and isinstance(value, (list, set)):
            item_type = args[0] if args else Any
            return {cls._parse_value(item_type, item) for item in value}
        if origin is tuple and isinstance(value, (list, tuple)):
            if len(args) == 2 and args[1] is Ellipsis:
                return tuple(cls._parse_value(args[0], item) for item in value)
            return tuple(
                cls._parse_value(item_type, item) for item_type, item in zip(args, value, strict=False)
            )
        if origin is dict and isinstance(value, Mapping):
            key_type, value_type = args if len(args) == 2 else (Any, Any)
            return {
                cls._parse_value(key_type, key): cls._parse_value(value_type, item)
                for key, item in value.items()
            }
        if isinstance(annotation, type):
            if isinstance(value, annotation):
                return value
            if issubclass(annotation, BaseRecord) and isinstance(value, Mapping):
                return annotation.parse(value)
            if is_dataclass(annotation) and isinstance(value, Mapping):
                hints = get_type_hints(annotation)
                return annotation(
                    **{
                        key: cls._parse_value(hints[key], item)
                        for key, item in value.items()
                        if key in hints
                    }
                )
            if issubclass(annotation, Enum):
                return annotation(value)
        return load_dt(annotation, value)

    @classmethod
    def parse(cls, data: Mapping[str, Any]) -> Self:
        hints = cls._type_hints()
        return cls(
            **{
                key: cls._parse_value(hints[key], value)
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
    """Internal record collection. Firmware jobs operate through Rows.

    Firmware Books set ``record_cls`` and ``row_cls``. CRUD goes through the Row.
    """

    record_cls: type[RecordT]
    row_cls: type[BaseRecordMixin]

    def __init__(self, factory: EngineFactory) -> None:
        self._factory = factory

    def _session(self):
        return self._factory.session()

    def add(self, record: RecordT) -> int:
        now = utcnow()
        prepared = replace(
            record,
            created_at=record.created_at or now,
            updated_at=now,
        )
        with self._session() as session:
            values = prepared.to_dict()
            values.pop("id", None)
            values = {key: value for key, value in values.items() if value is not None}
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
        with self._session() as session:
            row = session.get(type(self).row_cls, record.id)
            if row is None:
                return False
            values = record.to_dict()
            values.pop("id", None)
            values.pop("created_at", None)
            for key, value in values.items():
                if value is None:
                    continue
                setattr(row, key, value)
            row.updated_at = utcnow()
            session.commit()
            return True

    def upsert(self, record: RecordT) -> int:
        if record.id and self.exists(record.id):
            self.update(record)
            return record.id
        return self.add(record)

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
        applied = {key: value for key, value in filters.items() if value is not None}
        if applied:
            stmt = stmt.filter_by(**applied)
        with self._session() as session:
            return [type(self).record_cls.from_row(row) for row in session.scalars(stmt)]
