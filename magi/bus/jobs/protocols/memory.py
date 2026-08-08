"""Pure DTOs for local long-term memory."""

from __future__ import annotations

from dataclasses import dataclass

KIND_FACT = "fact"
KIND_QUICK_NOTE = "quick_note"
ALL_KINDS = frozenset({KIND_FACT, KIND_QUICK_NOTE})


@dataclass(frozen=True, slots=True)
class MemoryView:
    id: int
    uid: int
    kind: str
    subject: str
    body: str
    priority: int
    completed_at: str | None
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id, "uid": self.uid, "kind": self.kind, "subject": self.subject,
            "body": self.body, "priority": self.priority,
            "completed_at": self.completed_at, "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
