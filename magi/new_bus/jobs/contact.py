"""ContactJob + ContactNoteJob — writes to ``contacts`` + ``contact_notes`` tables.

Sync writes only.  The corresponding ``ContactBook`` /
``ContactNoteBook`` provide reads.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, select
from sqlalchemy.orm import Mapped, mapped_column

from magi.new_bus.db.engine import EngineFactory
from magi.new_bus.jobs.base import BaseJob, JobBase, job_utcnow_naive
from magi.new_bus.books.local.contact import (
    SOURCE_EVA,
    SOURCE_MANUAL,
    ROLE_ASSIGNED,
    ROLE_GUEST,
)

logger = logging.getLogger("magi.new_bus.jobs.contact")


class _JContactRow(JobBase):
    __tablename__ = "contacts"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(nullable=False)
    display_name: Mapped[str | None] = mapped_column(nullable=True)
    role: Mapped[str] = mapped_column(default=ROLE_GUEST, nullable=False)
    admin: Mapped[bool] = mapped_column(default=False, nullable=False)
    telegram_id: Mapped[int | None] = mapped_column(nullable=True)
    separated_at = mapped_column(DateTime, nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)
    source: Mapped[str] = mapped_column(default=SOURCE_MANUAL, nullable=False)
    last_seen_at = mapped_column(DateTime, default=job_utcnow_naive, nullable=False)
    created_at = mapped_column(DateTime, default=job_utcnow_naive, nullable=False)
    updated_at = mapped_column(
        DateTime, default=job_utcnow_naive, onupdate=job_utcnow_naive, nullable=False,
    )


class _JContactNoteRow(JobBase):
    __tablename__ = "contact_notes"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    contact_id: Mapped[int] = mapped_column(
        ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False,
    )
    note: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(default=SOURCE_EVA, nullable=False)
    kind: Mapped[str] = mapped_column(default="permanent", nullable=False)
    note_date = mapped_column(DateTime, nullable=True)
    created_at = mapped_column(DateTime, default=job_utcnow_naive, nullable=False)
    updated_at = mapped_column(
        DateTime, default=job_utcnow_naive, onupdate=job_utcnow_naive, nullable=False,
    )


class ContactJob(BaseJob):
    """Write side of the contact domain."""

    def __init__(self, factory: EngineFactory):
        super().__init__(factory)

    def add(
        self,
        *,
        name: str,
        role: str = ROLE_GUEST,
        display_name: str | None = None,
        admin: bool = False,
        telegram_id: int | None = None,
    ) -> int:
        with self._factory.session() as s:
            row = _JContactRow(
                name=name, role=role, display_name=display_name,
                admin=admin, telegram_id=telegram_id,
            )
            s.add(row)
            s.commit()
            s.refresh(row)
        return row.id

    def set_admin(self, *, contact_id: int, admin: bool = True) -> None:
        with self._factory.session() as s:
            row = s.scalar(
                select(_JContactRow).where(_JContactRow.id == contact_id)
            )
            if row is None:
                return
            row.admin = admin
            s.commit()

    def separate(self, *, contact_id: int) -> None:
        with self._factory.session() as s:
            row = s.scalar(
                select(_JContactRow).where(_JContactRow.id == contact_id)
            )
            if row is None:
                return
            row.separated_at = job_utcnow_naive()
            s.commit()


class ContactNoteJob(BaseJob):
    """Write side of the contact-notes domain."""

    def __init__(self, factory: EngineFactory):
        super().__init__(factory)

    def add(
        self,
        *,
        contact_id: int,
        note: str,
        source: str = SOURCE_EVA,
        kind: str = "permanent",
    ) -> int:
        with self._factory.session() as s:
            row = _JContactNoteRow(
                contact_id=contact_id, note=note, source=source, kind=kind,
            )
            s.add(row)
            s.commit()
            s.refresh(row)
        return row.id


__all__ = [
    "ContactJob",
    "ContactNoteJob",
    "ROLE_ASSIGNED",
    "ROLE_GUEST",
    "SOURCE_EVA",
    "SOURCE_MANUAL",
]
