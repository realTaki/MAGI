"""BUS-owned local long-term-memory repository/application service."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from magi.bus.contracts.memory import ALL_KINDS, KIND_ONGOING, MemoryView


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.isoformat() + "Z"
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _view(row) -> MemoryView:
    return MemoryView(
        id=int(row.id), uid=int(row.uid), kind=str(row.kind), subject=str(row.subject), body=str(row.body),
        importance=int(row.importance), source=str(row.source), completed_at=_iso(row.completed_at),
        created_at=_iso(row.created_at) or "", updated_at=_iso(row.updated_at) or "",
    )


class MemoryService:
    """All memory persistence; no ORM object crosses this boundary."""

    def __init__(self, state_dir: str) -> None:
        self._state_dir = state_dir

    def list_for_owner(
        self, uid: int, *, kind: str | None = None, include_completed: bool = False, limit: int = 50,
    ) -> list[MemoryView]:
        from magi.bus.models.local.memory import MemoryEntry
        from magi.bus._persistence import open_session
        with open_session(self._state_dir) as session:
            stmt = select(MemoryEntry).where(MemoryEntry.uid == uid)
            if kind is not None:
                stmt = stmt.where(MemoryEntry.kind == kind)
            if not include_completed:
                stmt = stmt.where((MemoryEntry.kind != KIND_ONGOING) | MemoryEntry.completed_at.is_(None))
            rows = session.scalars(
                stmt.order_by(MemoryEntry.importance.desc(), MemoryEntry.updated_at.desc()).limit(limit)
            ).all()
            return [_view(row) for row in rows]

    def list_recent(self, uid: int, *, limit: int = 20) -> list[MemoryView]:
        return self.list_for_owner(uid, include_completed=True, limit=limit)

    def get(self, memory_id: int) -> MemoryView | None:
        from magi.bus.models.local.memory import MemoryEntry
        from magi.bus._persistence import open_session
        with open_session(self._state_dir) as session:
            row = session.get(MemoryEntry, memory_id)
            return _view(row) if row is not None else None

    def add(
        self, uid: int, *, kind: str, subject: str, body: str, importance: int = 3, source: str = "eve",
    ) -> MemoryView:
        from magi.bus.models.local.memory import MemoryEntry
        from magi.bus._persistence import open_session
        if kind not in ALL_KINDS:
            raise ValueError(f"kind {kind!r} not in {sorted(ALL_KINDS)}")
        subject = subject.strip()[:200]
        body = body.strip()[: 8 * 1024]
        if not subject or not body:
            raise ValueError("subject and body are required")
        with open_session(self._state_dir) as session:
            row = MemoryEntry(
                uid=uid, kind=kind, subject=subject, body=body,
                importance=max(1, min(5, int(importance))), source=source,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return _view(row)

    def update(
        self, memory_id: int, *, subject: str | None = None, body: str | None = None,
        importance: int | None = None,
    ) -> MemoryView:
        from magi.bus.models.local.memory import MemoryEntry
        from magi.bus._persistence import open_session
        with open_session(self._state_dir) as session:
            row = session.get(MemoryEntry, memory_id)
            if row is None:
                raise LookupError(f"memory {memory_id!r} not found")
            if subject is not None:
                row.subject = subject.strip()[:200]
                if not row.subject:
                    raise ValueError("subject is required")
            if body is not None:
                row.body = body.strip()[: 8 * 1024]
                if not row.body:
                    raise ValueError("body is required")
            if importance is not None:
                row.importance = max(1, min(5, int(importance)))
            session.commit()
            session.refresh(row)
            return _view(row)

    def complete(self, memory_id: int) -> MemoryView:
        from magi.bus.models.local.memory import MemoryEntry
        from magi.bus._persistence import open_session
        from magi.bus._persistence.base import utcnow_naive
        with open_session(self._state_dir) as session:
            row = session.get(MemoryEntry, memory_id)
            if row is None:
                raise LookupError(f"memory {memory_id!r} not found")
            row.completed_at = utcnow_naive()
            session.commit()
            session.refresh(row)
            return _view(row)

    def delete(self, memory_id: int) -> bool:
        from magi.bus.models.local.memory import MemoryEntry
        from magi.bus._persistence import open_session
        with open_session(self._state_dir) as session:
            row = session.get(MemoryEntry, memory_id)
            if row is None:
                return False
            session.delete(row)
            session.commit()
            return True
