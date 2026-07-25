"""ContactStore — SQLite-backed CRUD for ``Contact`` notes.

The unified ``contacts`` table replaces the old
``contact_entries`` table. Each ``Contact`` row now carries
a ``notes`` field (free-form markdown, LLM-managed) and a
``source`` field (who recorded it).

The store is stateless, safe to instantiate per-request.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select

from magi.agent.db import Contact, open_session
from magi.agent.db.base import utcnow_naive
from magi.agent.db.models_contact import SOURCE_EVE

logger = logging.getLogger("magi.agent.memory.contacts.store")

_NOTES_MAX = 8 * 1024
_ROLE_MAX = 64


@dataclass(frozen=True)
class ContactView:
    """The in-memory shape returned to callers.
    Mirrors Contact fields relevant for the LLM tools.
    """

    id: int
    name: str
    display_name: str | None
    role: str | None
    notes: str
    source: str
    telegram_id: int | None
    last_seen_at: str
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: Contact) -> "ContactView":
        return cls(
            id=row.id,
            name=row.name,
            display_name=row.display_name,
            role=row.role,
            notes=row.notes,
            source=row.source,
            telegram_id=row.telegram_id,
            last_seen_at=row.last_seen_at.isoformat().replace("+00:00", "Z"),
            created_at=row.created_at.isoformat().replace("+00:00", "Z"),
            updated_at=row.updated_at.isoformat().replace("+00:00", "Z"),
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "display_name": self.display_name,
            "role": self.role,
            "notes": self.notes,
            "source": self.source,
            "telegram_id": self.telegram_id,
            "last_seen_at": self.last_seen_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class ContactStore:
    """Stateless CRUD wrapper for Contact notes."""

    state_dir: str | os.PathLike[str]

    # -- public -----------------------------------------------------------

    def add_note(
        self,
        contact_id: int,
        *,
        notes: str,
        role: Optional[str] = None,
        source: str = SOURCE_EVE,
    ) -> ContactView:
        """Append/add notes about a contact."""
        notes = notes.strip()[:_NOTES_MAX]
        if not notes:
            raise ValueError("notes is required")
        if role is not None:
            role = role.strip()[:_ROLE_MAX] or None

        with open_session() as db:
            row = db.get(Contact, contact_id)
            if row is None:
                # Create a new contact entry on the fly.
                row = Contact(
                    name=f"contact-{contact_id}",
                    role=role or "contact",
                    notes=notes,
                    source=source,
                    last_seen_at=utcnow_naive(),
                )
                db.add(row)
            else:
                row.notes = notes
                if role is not None:
                    row.role = role
                row.source = source
                row.last_seen_at = utcnow_naive()
            db.commit()
            db.refresh(row)
        logger.info("contact note updated", extra={"contact_id": row.id})
        return ContactView.from_row(row)

    def get(self, contact_id: int) -> Optional[ContactView]:
        with open_session() as db:
            row = db.get(Contact, contact_id)
        if row is None:
            return None
        return ContactView.from_row(row)

    def find_by_name(self, name: str) -> Optional[ContactView]:
        """Find a contact by exact name match."""
        with open_session() as db:
            row = db.scalar(
                select(Contact).where(Contact.name == name)
            )
        if row is None:
            return None
        return ContactView.from_row(row)

    def search(self, query: str, limit: int = 20) -> list[ContactView]:
        """Simple substring search on name and notes."""
        with open_session() as db:
            pattern = f"%{query}%"
            rows = db.scalars(
                select(Contact)
                .where(
                    (Contact.name.ilike(pattern))
                    | (Contact.notes.ilike(pattern))
                )
                .order_by(Contact.last_seen_at.desc())
                .limit(limit)
            ).all()
        return [ContactView.from_row(r) for r in rows]

    def list_with_notes(self, limit: int = 50) -> list[ContactView]:
        """All contacts that have non-empty notes."""
        with open_session() as db:
            rows = db.scalars(
                select(Contact)
                .where(Contact.notes != "")
                .order_by(Contact.last_seen_at.desc())
                .limit(limit)
            ).all()
        return [ContactView.from_row(r) for r in rows]

    def update_notes(
        self,
        contact_id: int,
        *,
        notes: Optional[str] = None,
        role: Optional[str] = None,
    ) -> ContactView:
        """Patch notes and/or role."""
        with open_session() as db:
            row = db.get(Contact, contact_id)
            if row is None:
                raise LookupError(f"contact {contact_id!r} not found")
            if notes is not None:
                row.notes = notes.strip()[:_NOTES_MAX]
            if role is not None:
                row.role = role.strip()[:_ROLE_MAX] or None
            row.last_seen_at = utcnow_naive()
            db.commit()
            db.refresh(row)
        return ContactView.from_row(row)

    def delete_notes(self, contact_id: int) -> bool:
        """Clear the notes field (no longer remember)."""
        with open_session() as db:
            row = db.get(Contact, contact_id)
            if row is None:
                return False
            row.notes = ""
            row.source = "manual"
            db.commit()
        return True


__all__ = [
    "ContactStore",
    "ContactView",
]
