"""BUS-owned local contact queries.

The public methods return immutable DTOs; SQLAlchemy rows stay inside the
short repository transaction.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select

from magi.bus.contracts.contact import ContactView, NoteView


def _iso(value: datetime | None) -> str | None:
    return value.isoformat().replace("+00:00", "Z") if value is not None else None


def _contact_view(row) -> ContactView:
    return ContactView(
        id=int(row.id), name=str(row.name), display_name=row.display_name, role=row.role,
        notes=str(row.notes), source=str(row.source), telegram_id=row.telegram_id,
        separated=row.separated_at is not None,
        last_seen_at=_iso(row.last_seen_at) or "", created_at=_iso(row.created_at) or "",
        updated_at=_iso(row.updated_at) or "",
    )


def _note_view(row) -> NoteView:
    return NoteView(
        id=int(row.id), contact_id=int(row.contact_id), note=str(row.note), source=str(row.source),
        kind=str(row.kind), note_date=_iso(row.note_date), created_at=_iso(row.created_at) or "",
        updated_at=_iso(row.updated_at) or "",
    )


class ContactsService:
    """Contact read facade used by agent and worker authorization paths."""

    def __init__(self, state_dir: str) -> None:
        self._state_dir = state_dir

    def role_for(self, uid: int) -> str | None:
        contact = self.get(uid)
        return contact.role if contact is not None else None

    def get(self, uid: int) -> ContactView | None:
        from magi.db import Contact, open_session
        with open_session(self._state_dir) as session:
            row = session.get(Contact, uid)
            return _contact_view(row) if row is not None else None

    def find_by_telegram_id(self, tgid: int) -> ContactView | None:
        from magi.db import Contact, open_session
        with open_session(self._state_dir) as session:
            row = session.scalar(select(Contact).where(Contact.telegram_id == tgid))
            return _contact_view(row) if row is not None else None

    def list_notes(self, uid: int) -> list[NoteView]:
        from magi.db import ContactNote, open_session
        with open_session(self._state_dir) as session:
            rows = session.scalars(
                select(ContactNote)
                .where(ContactNote.contact_id == uid)
                .order_by(ContactNote.created_at.desc())
            ).all()
            return [_note_view(row) for row in rows]

    def read_daily_note(self, uid: int) -> NoteView | None:
        from magi.db import ContactNote, open_session
        now = datetime.utcnow()
        today = datetime(now.year, now.month, now.day)
        with open_session(self._state_dir) as session:
            row = session.scalar(
                select(ContactNote).where(
                    ContactNote.contact_id == uid,
                    ContactNote.kind == "daily",
                    ContactNote.note_date == today,
                )
            )
            return _note_view(row) if row is not None else None

    def create_contact(
        self, *, name: str, display_name: str | None = None, role: str = "guest",
        telegram_id: int | None = None, notes: str = "", source: str = "eve",
    ) -> ContactView:
        from magi.db import Contact, open_session
        from magi.db.base import utcnow_naive

        normalized_name = name.strip()
        if not normalized_name:
            raise ValueError("name is required")
        with open_session(self._state_dir) as session:
            if session.scalar(select(Contact).where(Contact.name == normalized_name)) is not None:
                raise ValueError(f"contact name {normalized_name!r} already exists")
            row = Contact(
                name=normalized_name, display_name=(display_name or "").strip() or None,
                role=role.strip() or "guest", telegram_id=telegram_id, notes=notes.strip(),
                source=source, last_seen_at=utcnow_naive(),
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return _contact_view(row)

    def add_note(self, contact_id: int, note: str, *, source: str = "eve") -> NoteView:
        from magi.db import Contact, ContactNote, open_session
        from magi.db.base import utcnow_naive

        content = note.strip()[: 8 * 1024]
        if not content:
            raise ValueError("note is required")
        with open_session(self._state_dir) as session:
            contact = session.get(Contact, contact_id)
            if contact is None:
                raise ValueError(f"contact {contact_id!r} not found")
            row = ContactNote(contact_id=contact_id, note=content, source=source)
            session.add(row)
            contact.last_seen_at = utcnow_naive()
            session.commit()
            session.refresh(row)
            return _note_view(row)

    def update_note(self, note_id: int, note: str) -> NoteView:
        from magi.db import ContactNote, open_session
        from magi.db.base import utcnow_naive

        content = note.strip()[: 8 * 1024]
        if not content:
            raise ValueError("note is required")
        with open_session(self._state_dir) as session:
            row = session.get(ContactNote, note_id)
            if row is None:
                raise LookupError(f"contact_note {note_id!r} not found")
            row.note = content
            row.updated_at = utcnow_naive()
            session.commit()
            session.refresh(row)
            return _note_view(row)

    def delete_note(self, note_id: int) -> bool:
        from magi.db import ContactNote, open_session
        with open_session(self._state_dir) as session:
            row = session.get(ContactNote, note_id)
            if row is None:
                return False
            session.delete(row)
            session.commit()
            return True

    def upsert_daily_note(
        self, contact_id: int, body_delta: str, *, note_date: datetime | None = None,
    ) -> NoteView:
        from magi.db import Contact, ContactNote, open_session
        from magi.db.base import utcnow_naive

        content = body_delta.strip()[: 8 * 1024]
        if not content:
            raise ValueError("body_delta is required")
        if note_date is None:
            now = datetime.utcnow()
            note_date = datetime(now.year, now.month, now.day)
        with open_session(self._state_dir) as session:
            if session.get(Contact, contact_id) is None:
                raise ValueError(f"contact {contact_id!r} not found")
            row = session.scalar(select(ContactNote).where(
                ContactNote.contact_id == contact_id,
                ContactNote.kind == "daily",
                ContactNote.note_date == note_date,
            ))
            if row is None:
                row = ContactNote(
                    contact_id=contact_id, note=content, source="eve", kind="daily", note_date=note_date,
                )
                session.add(row)
            else:
                row.note = (row.note + "\n" + content)[: 32 * 1024]
                row.updated_at = utcnow_naive()
            session.commit()
            session.refresh(row)
            return _note_view(row)

    def search(self, query: str, *, limit: int = 20) -> list[ContactView]:
        from magi.db import Contact, ContactNote, open_session

        pattern = f"%{query}%"
        with open_session(self._state_dir) as session:
            rows = list(session.scalars(select(Contact).where(Contact.name.ilike(pattern)).limit(limit)))
            seen = {row.id for row in rows}
            matched_ids = set(session.scalars(
                select(ContactNote.contact_id).where(ContactNote.note.ilike(pattern)).distinct()
            ))
            for row in session.scalars(select(Contact).where(Contact.id.in_(matched_ids))).all():
                if row.id not in seen:
                    rows.append(row)
                    seen.add(row.id)
            rows.sort(key=lambda row: row.last_seen_at or datetime.min, reverse=True)
            return [_contact_view(row) for row in rows[:limit]]

    def set_telegram_id(self, uid: int, telegram_id: int | None) -> bool:
        from magi.db import Contact, open_session
        with open_session(self._state_dir) as session:
            row = session.get(Contact, uid)
            if row is None:
                return False
            row.telegram_id = telegram_id
            session.commit()
            return True

    def bind_telegram(self, uid: int, telegram_id: int) -> ContactView | None:
        """Atomically move a Telegram binding to an active contact."""
        from magi.db import Contact, open_session
        with open_session(self._state_dir) as session:
            contact = session.get(Contact, uid)
            if contact is None or contact.separated_at is not None:
                return None
            prior = session.scalar(select(Contact).where(Contact.telegram_id == telegram_id))
            if prior is not None and prior.id != contact.id:
                prior.telegram_id = None
            contact.telegram_id = telegram_id
            session.commit()
            session.refresh(contact)
            return _contact_view(contact)
