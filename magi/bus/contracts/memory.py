"""Pure DTOs for local long-term memory."""

from __future__ import annotations

from dataclasses import dataclass

KIND_IMPORTANT = "important"
KIND_ONGOING = "ongoing"
ALL_KINDS = frozenset({KIND_IMPORTANT, KIND_ONGOING})
SOURCE_MANUAL = "manual"
SOURCE_EVE = "eve"
SOURCE_SYSTEM = "system"


@dataclass(frozen=True, slots=True)
class MemoryView:
    id: int
    uid: int
    kind: str
    subject: str
    body: str
    importance: int
    source: str
    completed_at: str | None
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id, "uid": self.uid, "kind": self.kind, "subject": self.subject,
            "body": self.body, "importance": self.importance, "source": self.source,
            "completed_at": self.completed_at, "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
