"""BUS clock. Time on Book and Job is datetime; storage writes ISO-8601."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, get_args


def utcnow() -> datetime:
    return datetime.now(UTC)


def parse_dt(value: Any) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def dump_dt(value: Any) -> Any:
    return value.isoformat() if isinstance(value, datetime) else value


def load_dt(annotation: Any, value: Any) -> Any:
    if annotation is datetime or datetime in get_args(annotation):
        return parse_dt(value)
    return value
