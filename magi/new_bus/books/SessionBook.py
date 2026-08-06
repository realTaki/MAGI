"""SessionBook — 会话记录簿。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, select
from sqlalchemy.orm import Mapped, mapped_column

from magi.new_bus.books.base import BaseBook
from magi.new_bus.db.base import Base, utcnow_naive


@dataclass(frozen=True, slots=True)
class Session:
    session_id: str
    conversation_id: str
    summary: str | None = None


class _SessionRow(Base):
    __tablename__ = "chat_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    conversation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


class SessionBook(BaseBook[_SessionRow, Session]):
    model_cls = _SessionRow
    dto_cls = Session

    def get(self, *, session_id: str) -> Session | None:
        with self._session() as s:
            row = s.scalar(
                select(_SessionRow).where(_SessionRow.session_id == session_id)
            )
            return self._row_to_dto(row) if row else None

    def list_by_conversation(self, *, conversation_id: str) -> list[Session]:
        with self._session() as s:
            rows = s.scalars(
                select(_SessionRow)
                .where(_SessionRow.conversation_id == conversation_id)
                .order_by(_SessionRow.created_at.desc())
            ).all()
            return [self._row_to_dto(r) for r in rows]
