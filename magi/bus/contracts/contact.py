"""Pure DTOs for contact and contact-note facts."""

from __future__ import annotations

from dataclasses import dataclass

SOURCE_MANUAL = "manual"
SOURCE_EVE = "eve"
SOURCE_SYSTEM = "system"


@dataclass(frozen=True, slots=True)
class ContactView:
    id: int
    name: str
    display_name: str | None
    role: str | None
    notes: str
    source: str
    telegram_id: int | None
    separated: bool
    last_seen_at: str
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id, "name": self.name, "display_name": self.display_name,
            "role": self.role, "notes": self.notes, "source": self.source,
            "telegram_id": self.telegram_id, "separated": self.separated, "last_seen_at": self.last_seen_at,
            "created_at": self.created_at, "updated_at": self.updated_at,
        }


@dataclass(frozen=True, slots=True)
class NoteView:
    id: int
    contact_id: int
    note: str
    source: str
    kind: str
    note_date: str | None
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id, "contact_id": self.contact_id, "note": self.note,
            "source": self.source, "kind": self.kind, "note_date": self.note_date,
            "created_at": self.created_at, "updated_at": self.updated_at,
        }
