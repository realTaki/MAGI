
"""ORM model for the legacy ``settings`` key/value table.

The table started life as a tiny raw-SQL store for runtime configuration
(bot tokens, timezone, Telegram reaction preferences, and similar values).
It is now part of the shared SQLAlchemy metadata so reads and writes use the
same engine, transaction policy, WAL pragmas, and ``open_session()`` path as
the rest of the database.

The public ``state_get`` / ``state_set`` / ``state_delete`` functions remain
as a compatibility facade for callers that still use the old state-oriented
API. They are implemented in :mod:`magi.db.settings` and do not open a
second DB connection themselves.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from magi.db.base import Base


def _utcnow_sqlite_text() -> str:
    """Return the legacy SQLite timestamp shape used by ``settings``."""
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")


class Setting(Base):
    """One system-level key/value setting."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=_utcnow_sqlite_text,
        onupdate=_utcnow_sqlite_text,
        server_default=text("(datetime('now'))"),
    )

    def __repr__(self) -> str:
        # Do not include ``value`` — settings may contain credentials.
        return f"Setting(key={self.key!r}, updated_at={self.updated_at!r})"


__all__ = ["Setting"]
