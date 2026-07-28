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


def _utc_today_midnight() -> datetime:
    """Naive UTC midnight of "today".

    The daily-note row key. Local-timezone conversion happens
    at the *query* layer (system_settings timezone), not here
    — the row key stays UTC for deterministic date arithmetic.
    """
    now = datetime.utcnow()
    return datetime(now.year, now.month, now.day)

_NOTE_MAX = 8 * 1024
_ROLE_MAX = 64
_DAILY_NOTE_DELTA_MAX = 8 * 1024  # per single update_daily_note call
_DAILY_NOTE_BODY_MAX = 32 * 1024  # cumulative body ceiling per row


@dataclass(frozen=True)
class NoteView:
    """One contact-note row."""

    id: int
    contact_id: int
    note: str
    source: str
    kind: str
    note_date: str | None
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: ContactNote) -> "NoteView":
        return cls(
            id=row.id,
            contact_id=row.contact_id,
            note=row.note,
            source=row.source,
            kind=row.kind,
            note_date=(
                row.note_date.isoformat().replace("+00:00", "Z")
                if row.note_date is not None else None
            ),
            created_at=row.created_at.isoformat().replace("+00:00", "Z"),
            updated_at=row.updated_at.isoformat().replace("+00:00", "Z"),
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "contact_id": self.contact_id,
            "note": self.note,
            "source": self.source,
            "kind": self.kind,
            "note_date": self.note_date,
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
            ct.last_seen_at = utcnow_naive()
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

    # -- daily notes -------------------------------------------------------

    def upsert_daily_note(
        self,
        contact_id: int,
        body_delta: str,
        *,
        note_date: Optional[datetime] = None,
    ) -> NoteView:
        """Append ``body_delta`` to today's daily note for ``contact_id``.

        One row per (contact_id, kind='daily', note_date). If
        the row exists, append with a newline separator; else
        insert a new row. ``note_date`` defaults to today's
        naive UTC midnight.

        ``body_delta`` is capped at 32 KB on read — the LLM is
        expected to keep each call short (a sentence or two).
        The morning/night report reads the row verbatim
        (truncated to 8 KB before injecting into the prompt,
        same as the rest of the system's tool-result cap).

        Idempotent at the row level: re-calling with the
        same ``note_date`` appends, never duplicates. The
        partial unique index ``ux_contact_notes_daily`` keeps
        the row count honest under concurrent writes.
        """
        from sqlalchemy import update as _sa_update

        body_delta = body_delta.strip()[:_DAILY_NOTE_DELTA_MAX]
        if not body_delta:
            raise ValueError("body_delta is required")
        if note_date is None:
            note_date = _utc_today_midnight()

        with open_session() as db:
            ct = db.get(Contact, contact_id)
            if ct is None:
                raise ValueError(f"contact {contact_id!r} not found")

            existing = db.scalar(
                select(ContactNote).where(
                    ContactNote.contact_id == contact_id,
                    ContactNote.kind == "daily",
                    ContactNote.note_date == note_date,
                )
            )
            if existing is None:
                row = ContactNote(
                    contact_id=contact_id,
                    note=body_delta,
                    source=SOURCE_EVE,
                    kind="daily",
                    note_date=note_date,
                )
                db.add(row)
                ct.last_seen_at = utcnow_naive()
                db.commit()
                db.refresh(row)
                return NoteView.from_row(row)

            existing.note = (
                existing.note + "\n" + body_delta
            )[:_DAILY_NOTE_BODY_MAX]
            existing.updated_at = utcnow_naive()
            ct.last_seen_at = utcnow_naive()
            # ``update`` with synchronize_session keeps the
            # in-memory row in sync after we touch it
            # directly without going through ``db.flush()``.
            db.execute(
                _sa_update(ContactNote)
                .where(ContactNote.id == existing.id)
                .values(
                    note=existing.note,
                    updated_at=existing.updated_at,
                )
            )
            db.commit()
            db.refresh(existing)
            logger.info(
                "daily note appended",
                extra={"note_id": existing.id, "contact_id": contact_id},
            )
            return NoteView.from_row(existing)

    def read_daily_note(
        self,
        contact_id: int,
        *,
        note_date: Optional[datetime] = None,
    ) -> Optional[NoteView]:
        """Return today's daily note (or ``note_date``'s), or
        ``None`` if no row exists for that day.

        The morning/night report readers call this once per
        fire. Returns the single row keyed by
        ``(contact_id, kind='daily', note_date)`` — the partial
        unique index makes the query O(log n) on
        ``contact_notes``.
        """
        if note_date is None:
            note_date = _utc_today_midnight()
        with open_session() as db:
            row = db.scalar(
                select(ContactNote).where(
                    ContactNote.contact_id == contact_id,
                    ContactNote.kind == "daily",
                    ContactNote.note_date == note_date,
                )
            )
        return NoteView.from_row(row) if row else None

    def list_daily_notes(
        self,
        contact_id: int,
        *,
        limit: int = 14,
    ) -> list[NoteView]:
        """Recent daily notes for a contact, newest first.

        Used by the night summary's "this week" filter and
        future debugging surfaces. Default ``limit=14`` gives
        two weeks of history.
        """
        with open_session() as db:
            rows = db.scalars(
                select(ContactNote)
                .where(
                    ContactNote.contact_id == contact_id,
                    ContactNote.kind == "daily",
                )
                .order_by(ContactNote.note_date.desc())
                .limit(limit)
            ).all()
        return [NoteView.from_row(r) for r in rows]

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
