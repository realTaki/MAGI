"""ContactStore — CRUD for ``Contact`` + ``ContactNote``.

The ``contact_notes`` table holds individual facts per
contact.  The old ``contacts.notes`` text column is
kept for backward-compat but tools write to
``contact_notes`` exclusively.

The store is stateless, safe to instantiate per-request.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from sqlalchemy import select

from magi.agent.db import Contact, ContactNote, open_session
from magi.agent.db.base import utcnow_naive
from magi.agent.db.models_contact import SOURCE_EVE

logger = logging.getLogger("magi.agent.memory.contacts.store")

_NOTE_MAX = 8 * 1024
_ROLE_MAX = 64


@dataclass(frozen=True)
class NoteView:
    """One contact-note row."""

    id: int
    contact_id: int
    note: str
    source: str
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: ContactNote) -> "NoteView":
        return cls(
            id=row.id,
            contact_id=row.contact_id,
            note=row.note,
            source=row.source,
            created_at=row.created_at.isoformat().replace("+00:00", "Z"),
            updated_at=row.updated_at.isoformat().replace("+00:00", "Z"),
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "contact_id": self.contact_id,
            "note": self.note,
            "source": self.source,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class ContactView:
    """A contact + aggregated notes."""

    id: int
    name: str
    display_name: str | None
    role: str | None
    notes: str  # aggregated from contact_notes (legacy compat)
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
    """Stateless CRUD for contacts and their notes."""

    state_dir: str | os.PathLike[str]

    # -- contacts ----------------------------------------------------------

    def create_contact(
        self,
        name: str,
        *,
        display_name: Optional[str] = None,
        role: str = "guest",
        telegram_id: Optional[int] = None,
        notes: str = "",
        source: str = SOURCE_EVE,
    ) -> ContactView:
        """Create a new contact row.  Raises ``ValueError`` on dup name."""
        name = name.strip()
        if not name:
            raise ValueError("name is required")
        if display_name is not None:
            display_name = display_name.strip() or None
        role = role.strip() or "guest"

        with open_session() as db:
            existing = db.scalar(
                select(Contact).where(Contact.name == name)
            )
            if existing is not None:
                raise ValueError(
                    f"contact name {name!r} already exists (id={existing.id})"
                )
            row = Contact(
                name=name,
                display_name=display_name,
                role=role,
                telegram_id=telegram_id,
                notes=notes.strip(),
                source=source,
                last_seen_at=utcnow_naive(),
            )
            db.add(row)
            db.commit()
            db.refresh(row)
        logger.info("contact created", extra={"id": row.id, "name": row.name})
        return ContactView.from_row(row)

    def get(self, contact_id: int) -> Optional[ContactView]:
        with open_session() as db:
            row = db.get(Contact, contact_id)
        return ContactView.from_row(row) if row else None

    def find_by_name(self, name: str) -> Optional[ContactView]:
        with open_session() as db:
            row = db.scalar(select(Contact).where(Contact.name == name))
        return ContactView.from_row(row) if row else None

    def find_by_telegram_id(self, tgid: int) -> Optional[ContactView]:
        with open_session() as db:
            row = db.scalar(
                select(Contact).where(Contact.telegram_id == tgid)
            )
        return ContactView.from_row(row) if row else None

    # -- notes (contact_notes table) ---------------------------------------

    def add_note(
        self,
        contact_id: int,
        note: str,
        *,
        source: str = SOURCE_EVE,
    ) -> NoteView:
        """Create a new note row for an existing contact."""
        note = note.strip()[:_NOTE_MAX]
        if not note:
            raise ValueError("note is required")
        with open_session() as db:
            ct = db.get(Contact, contact_id)
            if ct is None:
                raise ValueError(f"contact {contact_id!r} not found")
            row = ContactNote(
                contact_id=contact_id,
                note=note,
                source=source,
            )
            db.add(row)
            contact.last_seen_at = utcnow_naive()
            db.commit()
            db.refresh(row)
        logger.info("contact note added", extra={"note_id": row.id, "contact_id": contact_id})
        return NoteView.from_row(row)

    def update_note(
        self,
        note_id: int,
        note: str,
    ) -> NoteView:
        """Update an existing note by id."""
        note = note.strip()[:_NOTE_MAX]
        if not note:
            raise ValueError("note is required")
        with open_session() as db:
            row = db.get(ContactNote, note_id)
            if row is None:
                raise LookupError(f"contact_note {note_id!r} not found")
            row.note = note
            row.updated_at = utcnow_naive()
            db.commit()
            db.refresh(row)
        return NoteView.from_row(row)

    def delete_note(self, note_id: int) -> bool:
        """Delete a note by id. Returns True if it existed."""
        with open_session() as db:
            row = db.get(ContactNote, note_id)
            if row is None:
                return False
            db.delete(row)
            db.commit()
        return True

    def list_notes(self, contact_id: int) -> list[NoteView]:
        """All notes for a contact, newest first."""
        with open_session() as db:
            rows = db.scalars(
                select(ContactNote)
                .where(ContactNote.contact_id == contact_id)
                .order_by(ContactNote.created_at.desc())
            ).all()
        return [NoteView.from_row(r) for r in rows]

    def get_note(self, note_id: int) -> Optional[NoteView]:
        with open_session() as db:
            row = db.get(ContactNote, note_id)
        return NoteView.from_row(row) if row else None

    # -- search ------------------------------------------------------------

    def search(
        self, query: str, limit: int = 20
    ) -> list[ContactView]:
        """Search contacts by name or note content."""
        with open_session() as db:
            pattern = f"%{query}%"
            # Find contacts whose notes match
            matching_contact_ids = set()
            note_rows = db.scalars(
                select(ContactNote.contact_id)
                .where(ContactNote.note.ilike(pattern))
                .distinct()
            ).all()
            matching_contact_ids.update(note_rows)

            # Find contacts whose name matches
            name_rows = db.scalars(
                select(Contact)
                .where(Contact.name.ilike(pattern))
                .limit(limit)
            ).all()

            # Merge and deduplicate
            results: list[Contact] = list(name_rows)
            seen_ids = {r.id for r in results}
            if matching_contact_ids:
                note_contacts = db.scalars(
                    select(Contact)
                    .where(Contact.id.in_(matching_contact_ids))
                    .order_by(Contact.last_seen_at.desc())
                    .limit(limit)
                ).all()
                for nc in note_contacts:
                    if nc.id not in seen_ids:
                        results.append(nc)
                        seen_ids.add(nc.id)

            results.sort(key=lambda r: r.last_seen_at or datetime.min, reverse=True)
            return [ContactView.from_row(r) for r in results[:limit]]


__all__ = [
    "ContactStore",
    "ContactView",
    "NoteView",
]
