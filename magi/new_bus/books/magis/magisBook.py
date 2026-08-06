"""MagisBook + MagisAdminBook — the ``magis`` tree + ``magis_admins`` rows.

Schema mirrors the old bus's ``magis`` + ``magis_admins`` tables.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, select
from sqlalchemy.orm import Mapped, mapped_column

from magi.new_bus.books.base import BaseBook
from magi.new_bus.db.base import Base, utcnow_naive


# -- public dataclasses --------------------------------------------------


@dataclass(frozen=True, slots=True)
class Magis:
    id: int
    name: str
    parent_id: int | None = None
    adam_id: int | None = None
    instruction: str = ""
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
