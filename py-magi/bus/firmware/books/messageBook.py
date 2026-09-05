"""MessageBook — current messages.

The record type :class:`Message` is the field list for this BaseBook.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import Boolean, DateTime, ForeignKey, Text, select
from sqlalchemy.orm import Mapped, mapped_column

from ...base.BaseBook import BaseBook, BaseRecord, BaseRecordMixin
from ...base.time import BaseTime, utcnow


@dataclass(kw_only=True)
class Message(BaseRecord):
    """One row in MessageBook.

    contact_id: speaker Contact.id
    content: non-empty text
    conversation_id: optional Conversation.id
    timestamp: when the message was produced
    archived: hidden from the live transcript
    """

    contact_id: int
    content: str
    conversation_id: int
    timestamp: BaseTime = field(default_factory=utcnow)
    archived: bool = False


class MessageRow(BaseRecordMixin):
    __tablename__ = "books_messages"

    contact_id: Mapped[int] = mapped_column(
        ForeignKey("books_contacts.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("books_conversations.id"), nullable=False
    )
    timestamp: Mapped[BaseTime] = mapped_column(DateTime, default=utcnow, nullable=False)
    archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class MessageBook(BaseBook[Message]):
    record_cls = Message
    row_cls = MessageRow

    def list(self, *, last_n: int | None = None, **filters: object) -> list[Message]:
        stmt = select(MessageRow)
        applied = {key: value for key, value in filters.items() if value is not None}
        if applied:
            stmt = stmt.filter_by(**applied)
        if last_n is None:
            stmt = stmt.order_by(MessageRow.id)
        else:
            stmt = stmt.order_by(MessageRow.id.desc()).limit(last_n)
        with self._session() as session:
            messages = [Message.from_row(row) for row in session.scalars(stmt)]
        return messages if last_n is None else list(reversed(messages))

    def search_conversation(
        self, *, conversation_id: int, q: str, limit: int
    ) -> list[Message]:
        needle = q.strip()
        if not needle or limit <= 0:
            return []
        stmt = (
            select(MessageRow)
            .where(
                MessageRow.conversation_id == conversation_id,
                MessageRow.content.ilike(self._like_pattern(needle), escape="\\"),
            )
            .order_by(MessageRow.id.desc())
            .limit(limit)
        )
        with self._session() as session:
            return [Message.from_row(row) for row in session.scalars(stmt)]

    def search_contact(self, *, contact_id: int, q: str, limit: int) -> list[Message]:
        needle = q.strip()
        if not needle or limit <= 0:
            return []
        stmt = (
            select(MessageRow)
            .where(
                MessageRow.contact_id == contact_id,
                MessageRow.content.ilike(self._like_pattern(needle), escape="\\"),
            )
            .order_by(MessageRow.id.desc())
            .limit(limit)
        )
        with self._session() as session:
            return [Message.from_row(row) for row in session.scalars(stmt)]

    @staticmethod
    def _like_pattern(q: str) -> str:
        escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        return f"%{escaped}%"
