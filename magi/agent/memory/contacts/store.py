"""ContactStore — SQLite-backed CRUD for ``ContactEntry``.

Same shape as :class:`magi.agent.memory.magi.store.MemoryStore`:
stateless, safe to instantiate per-request. The
``add`` path is upsert-style: if a row already exists
for the (owner, person) pair, the existing row is
patched instead of inserting a new one — keeps the
directory's "one row per person" invariant without
requiring the LLM to first check existence.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select

from magi.agent.db import open_session
from magi.agent.memory.contacts.models import (
    ContactEntry,
    SOURCE_EVE,
)


logger = logging.getLogger("magi.agent.memory.contacts.store")

_NOTES_MAX = 8 * 1024
_ROLE_MAX = 64


def _now_naive_utc() -> "datetime":
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).replace(tzinfo=None)


@dataclass(frozen=True)
class ContactView:
    """The in-memory shape returned to callers.

    Mirrors :class:`ContactEntry` (a SQLAlchemy row) but
    is decoupled from the ORM class — the LLM-facing
    tool result is JSON-serialised and a dataclass
    keeps the JSON shape stable across schema changes.
    """

    id: int
    owner_id: int
    person_id: Optional[int]
    role: Optional[str]
    notes: str
    source: str
    last_seen_at: str
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: ContactEntry) -> "ContactView":
        return cls(
            id=row.id,
            owner_id=row.owner_id,
            person_id=row.person_id,
            role=row.role,
            notes=row.notes,
            source=row.source,
            last_seen_at=row.last_seen_at.isoformat().replace("+00:00", "Z"),
            created_at=row.created_at.isoformat().replace("+00:00", "Z"),
            updated_at=row.updated_at.isoformat().replace("+00:00", "Z"),
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "owner_id": self.owner_id,
            "person_id": self.person_id,
            "role": self.role,
            "notes": self.notes,
            "source": self.source,
            "last_seen_at": self.last_seen_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class ContactStore:
    """Stateless CRUD wrapper for :class:`ContactEntry`.

    Single ``state_dir`` arg kept for caller compat —
    the actual path is resolved once per process via
    the ORM engine singleton.
    """

    state_dir: str | os.PathLike[str]

    # -- public -----------------------------------------------------------

    def upsert(
        self,
        owner_id: int,
        person_id: int,
        *,
        notes: str,
        role: Optional[str] = None,
        source: str = SOURCE_EVE,
    ) -> ContactView:
        """Insert or update the (owner, person) row.

        ``upsert`` instead of ``add`` because the LLM
        often learns new things about the same person
        in different turns; we want a single
        cumulative row rather than a journal.

        ``last_seen_at`` is bumped to "now" on every
        call so the per-chat prompt can prefer
        recently-touched people.
        """
        notes = notes.strip()[:_NOTES_MAX]
        if not notes:
            raise ValueError("notes is required")
        if role is not None:
            role = role.strip()[:_ROLE_MAX] or None

        with open_session() as db:
            row = db.execute(
                select(ContactEntry).where(
                    ContactEntry.owner_id == owner_id,
                    ContactEntry.person_id == person_id,
                )
            ).scalar_one_or_none()
            now = _now_naive_utc()
            if row is None:
                row = ContactEntry(
                    owner_id=owner_id,
                    person_id=person_id,
                    role=role,
                    notes=notes,
                    source=source,
                    last_seen_at=now,
                )
                db.add(row)
            else:
                row.notes = notes
                row.role = role
                row.last_seen_at = now
            db.commit()
            db.refresh(row)
        logger.info(
            "contact upserted",
            extra={
                "contact_id": row.id,
                "owner_id": owner_id,
                "person_id": person_id,
            },
        )
        return ContactView.from_row(row)

    def get(self, contact_id: int) -> Optional[ContactView]:
        with open_session() as db:
            row = db.get(ContactEntry, contact_id)
        if row is None:
            return None
        return ContactView.from_row(row)

    def find_by_person(
        self,
        owner_id: int,
        person_id: int,
    ) -> Optional[ContactView]:
        """Return the single contact row for a person.

        The (owner, person) unique index guarantees
        at most one row, so ``scalar_one_or_none`` is
        the right read.
        """
        with open_session() as db:
            row = db.execute(
                select(ContactEntry).where(
                    ContactEntry.owner_id == owner_id,
                    ContactEntry.person_id == person_id,
                )
            ).scalar_one_or_none()
        if row is None:
            return None
        return ContactView.from_row(row)

    def list_for_owner(
        self,
        owner_id: int,
        *,
        limit: int = 50,
    ) -> list[ContactView]:
        """All contacts owned by ``owner_id``, ordered
        by ``last_seen_at`` desc (most recent first).

        Used by the WebUI directory view, not by the
        system-prompt formatter (the formatter only
        includes the current chatter's contact, not
        the whole directory).
        """
        with open_session() as db:
            rows = db.execute(
                select(ContactEntry)
                .where(ContactEntry.owner_id == owner_id)
                .order_by(ContactEntry.last_seen_at.desc())
                .limit(limit)
            ).scalars().all()
        return [ContactView.from_row(r) for r in rows]

    def update(
        self,
        contact_id: int,
        *,
        notes: Optional[str] = None,
        role: Optional[str] = None,
    ) -> ContactView:
        """Patch one or more mutable fields."""
        with open_session() as db:
            row = db.get(ContactEntry, contact_id)
            if row is None:
                raise LookupError(f"contact {contact_id!r} not found")
            if notes is not None:
                row.notes = notes.strip()[:_NOTES_MAX]
            if role is not None:
                row.role = role.strip()[:_ROLE_MAX] or None
            row.last_seen_at = _now_naive_utc()
            db.commit()
            db.refresh(row)
        logger.info(
            "contact updated",
            extra={"contact_id": contact_id},
        )
        return ContactView.from_row(row)

    def delete(self, contact_id: int) -> bool:
        """Remove one row. ``True`` if it existed."""
        with open_session() as db:
            row = db.get(ContactEntry, contact_id)
            if row is None:
                return False
            db.delete(row)
            db.commit()
        logger.info(
            "contact deleted",
            extra={"contact_id": contact_id},
        )
        return True


__all__ = [
    "ContactStore",
    "ContactView",
]