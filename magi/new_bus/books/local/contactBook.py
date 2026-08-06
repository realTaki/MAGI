"""ContactBook + ContactNoteBook — unified person directory + per-fact notes.

Two tables:
- ``contacts``       — one row per person
- ``contact_notes``  — one row per fact (kind='permanent') or daily log (kind='daily')

Schema mirrors the old bus's ``contacts`` + ``contact_notes`` tables.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    select,
)
from sqlalchemy.orm import Mapped, mapped_column

from magi.new_bus.books.base import BaseBook
from magi.new_bus.db.base import Base, utcnow_naive


SOURCE_MANUAL = "manual"
SOURCE_EVA = "eva"
SOURCE_SYSTEM = "system"

ROLE_ASSIGNED = "assigned"
ROLE_GUEST = "guest"
ALL_ROLES = frozenset({ROLE_ASSIGNED, ROLE_GUEST})


# -- public dataclasses --------------------------------------------------


@dataclass(frozen=True, slots=True)
class Contact:
    id: int
    name: str
    display_name: str | None = None
    role: str = ROLE_GUEST
    admin: bool = False
    telegram_id: int | None = None
    separated_at: datetime | None = None
    last_seen_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
