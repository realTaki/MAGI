"""BUS clock. Time on Book and Job is BaseTime; JSON writes ISO-8601."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, get_args


class BaseTime(datetime):
    """Naive UTC time. JSON encoding writes ISO-8601."""

    def __new__(cls, *args, **kwargs):
        kwargs.pop("tzinfo", None)
        return datetime.__new__(cls, *args, **kwargs)

    @classmethod
    def parse(cls, value: Any) -> BaseTime | None:
        if value is None:
            return None
        if type(value) is cls:
            return value
        if isinstance(value, datetime):
            if value.tzinfo is not None:
                value = value.replace(tzinfo=None)
            return cls(
                value.year,
                value.month,
                value.day,
                value.hour,
                value.minute,
                value.second,
                value.microsecond,
            )
        parsed = datetime.fromisoformat(str(value))
        return cls.parse(parsed)


def utcnow() -> BaseTime:
    now = datetime.now(UTC).replace(tzinfo=None)
    return BaseTime(now.year, now.month, now.day, now.hour, now.minute, now.second, now.microsecond)


def dump_json(value: Any) -> str:
    def default(item: Any) -> str:
        if isinstance(item, datetime):
            return item.isoformat()
        raise TypeError(f"Object of type {type(item).__name__} is not JSON serializable")

    return json.dumps(value, default=default)


def load_dt(annotation: Any, value: Any) -> Any:
    types = (annotation, *get_args(annotation))
    if BaseTime in types or datetime in types:
        return BaseTime.parse(value)
    return value
