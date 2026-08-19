"""SQLAlchemy base for Book / Job tables. Schema changes go through Alembic."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from .time import utcnow


class BaseRecordMixin(DeclarativeBase):
    """Shared ORM columns for every Book / Job table."""

    __abstract__ = True

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )
