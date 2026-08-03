"""BUS-owned CRUD for durable operator action items."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from magi.bus.contracts.action_item import ActionItemView


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.isoformat() + "Z"
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _view(row) -> ActionItemView:
    return ActionItemView(
        id=int(row.id), uid=row.uid, kind=str(row.kind), title=str(row.title),
        description=row.description, target_url=row.target_url, priority=str(row.priority),
        due_date=_iso(row.due_date), source=str(row.source), created_at=_iso(row.created_at) or "",
        completed_at=_iso(row.completed_at), completed_by_uid=row.completed_by_uid,
        completion_note=row.completion_note, dismissed=bool(row.dismissed),
    )


class ActionItemService:
    """Action-item application service. ORM rows never leave this class."""

    def __init__(self, state_dir: str) -> None:
        self._state_dir = state_dir

    def create_llm(
        self, *, uid: int, kind: str, title: str, description: str | None,
        target_url: str | None, priority: str, due_date: datetime | None,
    ) -> ActionItemView:
        from magi.db import ActionItem, open_session

        with open_session(self._state_dir) as session:
            row = ActionItem(
                uid=uid, kind=kind, title=title, description=description,
                target_url=target_url, priority=priority, due_date=due_date, source="llm",
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return _view(row)

    def complete_for_owner(
        self, *, action_item_id: int, owner_uid: int, note: str | None,
    ) -> ActionItemView | None:
        from magi.db import ActionItem, open_session

        with open_session(self._state_dir) as session:
            row = session.get(ActionItem, action_item_id)
            if row is None or row.uid != owner_uid:
                return None
            if row.completed_at is None:
                row.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
                row.completed_by_uid = owner_uid
                if note is not None:
                    row.completion_note = note
                session.commit()
                session.refresh(row)
            return _view(row)

    def list_llm_for_owner(self, *, owner_uid: int, include_completed: bool) -> list[ActionItemView]:
        from magi.db import ActionItem, open_session

        with open_session(self._state_dir) as session:
            stmt = select(ActionItem).where(
                ActionItem.uid == owner_uid,
                ActionItem.kind.like("llm_action_item_%"),
            )
            if not include_completed:
                stmt = stmt.where(ActionItem.completed_at.is_(None), ActionItem.dismissed.is_(False))
            rows = session.scalars(
                stmt.order_by(
                    ActionItem.completed_at.is_(None).desc(),
                    ActionItem.priority.desc(),
                    ActionItem.created_at.desc(),
                )
            ).all()
            return [_view(row) for row in rows]
