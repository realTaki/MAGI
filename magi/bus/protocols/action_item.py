"""DTOs for the operator action-item surface."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ActionItemView:
    """JSON-safe action-item record returned by the BUS."""

    id: int
    uid: int | None
    kind: str
    title: str
    description: str | None
    target_url: str | None
    priority: str
    due_date: str | None
    source: str
    created_at: str
    completed_at: str | None
    completed_by_uid: int | None
    completion_note: str | None
    dismissed: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "uid": self.uid,
            "kind": self.kind,
            "title": self.title,
            "description": self.description,
            "target_url": self.target_url,
            "priority": self.priority,
            "due_date": self.due_date,
            "source": self.source,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "completed_by_uid": self.completed_by_uid,
            "completion_note": self.completion_note,
            "dismissed": self.dismissed,
        }
