"""MAGIS DTOs (public MAGI Society data; PG-backed)."""

from __future__ import annotations

# Provisional — these were never broken out as DTOs in the legacy
# codebase.  The services return ORM models today; the no-ORM-leak rule
# says they should return these DTOs once the entities fully migrate.
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True, slots=True)
class RuntimeIdentity:
    """Identity of one public MAGI runtime."""

    magic_id: int
    name: str
    display_name: Optional[str]
    description: Optional[str]
    created_at: Optional[str]
    last_active_at: Optional[str]


@dataclass(frozen=True, slots=True)
class MemberRole:
    """One member's role in a MAGIS group."""

    group_id: int
    magic_id: int
    role: str
    granted_at: Optional[str]
    granted_by_magic_id: Optional[int]


@dataclass(frozen=True, slots=True)
class ProviderConfiguration:
    """Credentials and model selection for the current MAGIC runtime.

    This is deliberately a value object: agent code may use it to construct
    an LLM client without receiving the underlying MAGIS ORM row.
    """

    provider: str
    api_key: str
    model: Optional[str]
