"""BaseRecord — one row. Book, Job, and JobResult all use this shape."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields, replace
from datetime import datetime
from typing import Any, Self, get_args, get_origin, get_type_hints

from .time import dump_dt, load_dt


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
        """Build a record from a mapping, keeping only declared fields."""
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
        """Apply declared, non-owned fields from ``changes`` onto this record."""
        hints = get_type_hints(type(self))
        allowed = {item.name for item in fields(type(self))} - (OWNED_FIELDS - {"updated_at"})
        updates = {
            key: load_dt(hints.get(key), value)
            for key, value in changes.items()
            if key in allowed
        }
        return replace(self, **updates)


OWNED_FIELDS = frozenset(item.name for item in fields(BaseRecord))


def field_kinds(record_cls: type[BaseRecord]) -> tuple[tuple[str, str], ...]:
    """SQL-facing kinds for each declared field: int, str, bool, datetime, json."""
    hints = get_type_hints(record_cls)
    return tuple((item.name, _field_kind(hints.get(item.name))) for item in fields(record_cls))


def _field_kind(annotation: Any) -> str:
    args = tuple(arg for arg in (get_args(annotation) or (annotation,)) if arg is not type(None))
    if not args:
        return "str"
    if bool in args or args[0] is bool:
        return "bool"
    if datetime in args or args[0] is datetime:
        return "datetime"
    if int in args or args[0] is int:
        return "int"
    origin = get_origin(args[0])
    if args[0] in (dict, list) or origin in (dict, list):
        return "json"
    return "str"
