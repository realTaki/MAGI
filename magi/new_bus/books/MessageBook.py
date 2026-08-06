"""MessageBook — 消息记录簿。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, select
from sqlalchemy.orm import Mapped, mapped_column

from magi.new_bus.books.base import BaseBook
from magi.new_bus.db.base import Base, utcnow_naive


@dataclass(frozen=True, slots=True)
class Message:
    message_id: str
    session_id: str
    role: str
    content: str


class _MessageRow(Base):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    message_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    session_id: Mapped[str] = mapped_column(String(64), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


class MessageBook(BaseBook[_MessageRow, Message]):
    model_cls = _MessageRow
    dto_cls = Message

    def list_by_session(self, *, session_id: str) -> list[Message]:
        with self._session() as s:
            rows = s.scalars(
                select(_MessageRow)
                .where(_MessageRow.session_id == session_id)
                .order_by(_MessageRow.created_at)
            ).all()
            return [self._row_to_dto(r) for r in rows]
