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

    def ensure_llm_credentials_item(self, *, owner_uid: int) -> bool:
        """Create the onboarding credential nudge once for an operator.

        The open-item lookup and insert share one BUS-owned transaction.  The
        database partial unique index remains the final concurrency guard.
        """
        from magi.db import ActionItem, open_session

        with open_session(self._state_dir) as session:
            existing = session.scalar(select(ActionItem).where(
                ActionItem.uid == owner_uid,
                ActionItem.kind == "llm_credentials_missing",
                ActionItem.completed_at.is_(None),
                ActionItem.dismissed.is_(False),
            ))
            if existing is not None:
                return False
            session.add(ActionItem(
                uid=owner_uid,
                kind="llm_credentials_missing",
                title="设置你的 LLM provider 和 API key",
                description="切到「Contacts」，找到自己的档案，把 Provider 和 API Key 填上。",
                target_url="/dashboard?tab=organization",
                priority="normal",
                source="system",
            ))
            session.commit()
            return True

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

    def get(self, action_item_id: int) -> ActionItemView | None:
        """Return one action item without exposing its ORM row."""
        from magi.db import ActionItem, open_session

        with open_session(self._state_dir) as session:
            row = session.get(ActionItem, action_item_id)
            return _view(row) if row is not None else None

    def list_for_owner(
        self,
        *,
        owner_uid: int,
        include_completed: bool,
        kind: str | None = None,
        completed_visible_days: int = 7,
    ) -> list[ActionItemView]:
        """List an owner's open items and optionally their recent completions."""
        from datetime import timedelta

        from magi.db import ActionItem, open_session

        with open_session(self._state_dir) as session:
            stmt = select(ActionItem).where(ActionItem.uid == owner_uid)
            if kind is not None:
                stmt = stmt.where(ActionItem.kind == kind)
            if not include_completed:
                stmt = stmt.where(
                    ActionItem.completed_at.is_(None),
                    ActionItem.dismissed.is_(False),
                )
            else:
                cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
                    days=completed_visible_days
                )
                stmt = stmt.where(
                    (ActionItem.completed_at.is_(None))
                    | (ActionItem.completed_at >= cutoff)
                )
            rows = session.scalars(stmt.order_by(
                ActionItem.completed_at.is_(None).desc(),
                ActionItem.priority.desc(),
                ActionItem.created_at.desc(),
            )).all()
            return [_view(row) for row in rows]

    def list_llm_for_owner(self, *, owner_uid: int, include_completed: bool) -> list[ActionItemView]:
        from magi.db import ActionItem, open_session

        with open_session(self._state_dir) as session:
            stmt = select(ActionItem).where(
                ActionItem.uid == owner_uid,
                ActionItem.kind.like("llm_action_item_%"),
            )
            if not include_completed:
                stmt = stmt.where(ActionItem.completed_at.is_(None), ActionItem.dismissed.is_(False))
            rows = session.scalars(stmt.order_by(
                ActionItem.completed_at.is_(None).desc(),
                ActionItem.priority.desc(),
                ActionItem.created_at.desc(),
            )).all()
            return [_view(row) for row in rows]
