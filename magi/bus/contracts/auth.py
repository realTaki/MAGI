"""Auth DTOs (caller identity for tool worker authorization)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True, slots=True)
class CallerIdentity:
    """Per-invocation identity passed to a tool's ``.run(ctx)``."""

    uid: int
    role: str
    channel: str
    session_id: str = ""
    extra: Optional[dict] = None
