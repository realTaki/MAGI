"""Shared store helpers."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from .errors import BackendError

_COLLECTION = re.compile(r"^[A-Za-z0-9_.-]+$")


def check_collection(name: str) -> str:
    if not name or not _COLLECTION.fullmatch(name):
        raise BackendError(f"invalid collection name {name!r}")
    return name


def coerce_id(value: Any) -> int | None:
    """Return a persisted id, or None when the record is still unassigned (0)."""
    if value in (None, "", 0, "0"):
        return None
    return int(value)


def next_id(existing: list[int]) -> int:
    return max(existing, default=0) + 1


def matches(record: Mapping[str, Any], eq: Mapping[str, Any] | None) -> bool:
    if not eq:
        return True
    return all(record.get(key) == value for key, value in eq.items())


def sort_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(records, key=lambda item: (str(item.get("created_at") or ""), int(item["id"])))


def copy_record(record: Mapping[str, Any]) -> dict[str, Any]:
    return dict(record)
