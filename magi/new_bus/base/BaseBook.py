"""BaseBook is BUS-internal current state. External modules never hold this object."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields, replace
from types import UnionType
from typing import Any, ClassVar, Self, Union, get_args, get_origin, get_type_hints

from .backends.backend import DatabaseBackend
from .errors import BookNotFoundError, InvalidJobError
from .time import utcnow


def _annotation_args(annotation: Any) -> tuple[Any, ...]:
    origin = get_origin(annotation)
    if origin in (Union, UnionType):
        return get_args(annotation)
    return (annotation,)


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
        hints = get_type_hints(cls)
        allowed = {item.name for item in fields(cls)}
        kwargs: dict[str, Any] = {}
        for key, value in data.items():
            if key not in allowed:
                continue
            annotation = hints.get(key)
            if value is None and annotation is not None and type(None) not in _annotation_args(
                annotation
            ):
                continue
            kwargs[key] = value
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

    name: ClassVar[str] = ""
    record_cls: ClassVar[type[BaseRecord]] = BaseRecord

    def __init__(self, backend) -> None:
        if not type(self).name:
            raise InvalidJobError(f"{type(self).__name__} must set class variable name")
        self._require_backend(backend)
        self._store = backend.records(f"books.{type(self).name}")

    def _require_backend(self, backend) -> None:
        if not isinstance(backend, DatabaseBackend):
            raise InvalidJobError("BaseBook requires a database backend")

    def add(self, record: BaseRecord) -> BaseRecord:
        if record.id and self._store.get(record.id) is not None:
            raise InvalidJobError(f"book {self.name!r} already has id {record.id}")
        now = utcnow()
        prepared = replace(
            record,
            created_at=record.created_at or now,
            updated_at=now,
        )
        return self.record_cls.parse(self._store.insert(prepared.to_dict()))

    def get(self, record_id: int) -> BaseRecord | None:
        data = self._store.get(record_id)
        return None if data is None else self.record_cls.parse(data)

    def require(self, record_id: int) -> bool:
        return self.get(record_id) is not None

    def update(self, record: BaseRecord) -> BaseRecord:
        if not record.id:
            raise InvalidJobError("update requires record.id")
        current = self.get(record.id)
        if current is None:
            raise BookNotFoundError(f"book {self.name!r} has no id {record.id}")
        stored = replace(
            record, id=current.id, created_at=current.created_at, updated_at=utcnow()
        )
        self._store.replace(stored.id, stored.to_dict())
        return stored

    def delete(self, record_id: int) -> bool:
        return self._store.delete(record_id)

    def list(self, filters: Mapping[str, Any] | None = None) -> list[BaseRecord]:
        return [self.record_cls.parse(row) for row in self._store.find(eq=filters)]
