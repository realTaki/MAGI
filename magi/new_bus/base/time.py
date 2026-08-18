"""BUS clock. Time is always an ISO-8601 UTC string."""

from __future__ import annotations

from datetime import UTC, datetime


def utcnow() -> str:
    return datetime.now(UTC).isoformat()
