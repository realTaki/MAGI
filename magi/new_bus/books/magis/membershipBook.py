"""MagisMembershipBook + MagisRoleBook — ``magis_memberships`` + ``magis_roles``.

Schema mirrors the old bus's tables.  Each ``MAGIS`` has at least
two reserved roles (``ADAM`` and ``EVA``) created by
:meth:`ensure_default_roles` (a caller-side helper, not a Book method
— Books are pure CRUD).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    select,
)
from sqlalchemy.orm import Mapped, mapped_column

from magi.new_bus.books.base import BaseBook
from magi.new_bus.db.base import Base, utcnow_naive


RESERVED_ROLE_NAMES = frozenset({"ADAM", "EVA"})
DEFAULT_ROLE_INSTRUCTIONS = {
    "ADAM": "You are the team leader for this MAGIS. Coordinate work, clarify goals, and surface conflicts or risks to the administrator.",
    "EVA": "You are a general-purpose member of this MAGIS. Collaborate with the team, carry out assigned work carefully, and report blockers clearly.",
}


# -- public dataclasses --------------------------------------------------


@dataclass(frozen=True, slots=True)
class MagisRole:
    id: int
    magis_id: int
    name: str
    instruction: str = ""
    is_reserved: bool = False
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
