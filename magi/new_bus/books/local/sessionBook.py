"""SessionBook + MessageBook — chat session and message transcript.

Two tables:
- ``chat_sessions``  — one row per chat session (Crockford ULID primary key)
- ``chat_messages``  — one row per persisted transcript message

Schema mirrors the old bus's ``chat_sessions`` + ``chat_messages`` tables.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import (
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    select,
)
from sqlalchemy.orm import Mapped, mapped_column

from magi.new_bus.books.base import BaseBook
from magi.new_bus.db.base import Base


# -- public dataclasses --------------------------------------------------


@dataclass(frozen=True, slots=True)
class Session:
    session_id: str
    delivery_address: str
    uid: int
    channel: str
    title: str | None = None
    active_tail_count: int = 20
    last_compaction_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


@dataclass(frozen=True, slots=True)
