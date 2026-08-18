"""Shared store helpers."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from ...errors import BackendError

_COLLECTION = re.compile(r"^[A-Za-z0-9_.-]+$")


def check_collection(name: str) -> str:
    if not name or not _COLLECTION.fullmatch(name):
        raise BackendError(f"invalid collection name {name!r}")
    return name


def require_id(record: Mapping[str, Any]) -> str:
    record_id = record.get("id")
    if not record_id:
        raise BackendError("record id is required")
    return str(record_id)


def matches(record: Mapping[str, Any], eq: Mapping[str, Any] | None) -> bool:
    if not eq:
        return True
    return all(record.get(key) == value for key, value in eq.items())


def sort_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(records, key=lambda item: (str(item.get("created_at") or ""), str(item["id"])))


def copy_record(record: Mapping[str, Any]) -> dict[str, Any]:
    return dict(record)
