"""SessionBook + MessageBook — chat session and message transcript.

Two tables:
- ``chat_sessions``  — one row per chat session (Crockford ULID primary key)
- ``chat_messages``  — one row per persisted transcript message

Schema mirrors the old bus's ``chat_sessions`` + ``chat_messages`` tables.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import (
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    select,
)
from sqlalchemy.orm import Mapped, mapped_column

from magi.new_bus.library.base import BaseBook
from magi.new_bus.db.base import Base


# -- public dataclasses --------------------------------------------------


@dataclass(frozen=True, slots=True)
class Session:
    session_id: str
    delivery_address: str
    uid: int
    channel: str
    title: str | None = None
    active_tail_count: int = 20
    last_compaction_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


@dataclass(frozen=True, slots=True)
class Message:
    id: int
    session_id: str
    message_id: str
    role: str
    text: str
    ts: str
    archived: int = 0
    content_blocks: list[dict[str, Any]] | None = None
    run_id: str | None = None
    llm_attempt_id: str | None = None


# -- internal ORM --------------------------------------------------------


class _SessionRow(Base):
    __tablename__ = "chat_sessions"

    session_id: Mapped[str] = mapped_column(String(26), primary_key=True)
    delivery_address: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True
    )
    uid: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    title: Mapped[str | None] = mapped_column(String(80), nullable=True)
    active_tail_count: Mapped[int] = mapped_column(
        Integer, default=20, nullable=False
    )
    last_compaction_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(32), nullable=False, index=True)


class _MessageRow(Base):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(
        String(26),
        ForeignKey("chat_sessions.session_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    message_id: Mapped[str] = mapped_column(String(26), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    ts: Mapped[str] = mapped_column(String(32), nullable=False)
    archived: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    content_blocks: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSON, nullable=True
    )
    run_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    llm_attempt_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    __table_args__ = (
        Index("ix_chat_messages_session_archived", "session_id", "archived", "id"),
        UniqueConstraint(
            "session_id", "message_id", name="uq_chat_messages_session_msg"
        ),
    )


# -- Books ---------------------------------------------------------------


class SessionBook(BaseBook[_SessionRow, Session]):
    model_cls = _SessionRow
    dto_cls = Session

    def get(self, *, session_id: str) -> Session | None:
        with self._factory.session() as s:
            row = s.scalar(
                select(_SessionRow).where(_SessionRow.session_id == session_id)
            )
            return self._row_to_dto(row) if row else None

    def list_for_owner(self, *, uid: int) -> list[Session]:
        with self._factory.session() as s:
            rows = s.scalars(
                select(_SessionRow)
                .where(_SessionRow.uid == uid)
                .order_by(_SessionRow.updated_at.desc())
            ).all()
            return [self._row_to_dto(r) for r in rows]

    def add(self, *, session_id: str, delivery_address: str, uid: int,
            channel: str, title: str | None = None,
            created_at: str = "", updated_at: str = "") -> Session:
        with self._factory.session() as s:
            row = _SessionRow(
                session_id=session_id,
                delivery_address=delivery_address,
                uid=uid,
                channel=channel,
                title=title,
                created_at=created_at,
                updated_at=updated_at,
            )
            s.add(row)
            s.commit()
            s.refresh(row)
        return self._row_to_dto(row)

    def touch(self, *, session_id: str, updated_at: str) -> None:
        with self._factory.session() as s:
            row = s.scalar(
                select(_SessionRow).where(_SessionRow.session_id == session_id)
            )
            if row is None:
                return
            row.updated_at = updated_at
            s.commit()


class MessageBook(BaseBook[_MessageRow, Message]):
    model_cls = _MessageRow
    dto_cls = Message

    def get(self, *, message_id: int) -> Message | None:
        with self._factory.session() as s:
            row = s.scalar(select(_MessageRow).where(_MessageRow.id == message_id))
            return self._row_to_dto(row) if row else None

    def list_for_session(self, *, session_id: str,
                         include_archived: bool = False) -> list[Message]:
        with self._factory.session() as s:
            stmt = select(_MessageRow).where(_MessageRow.session_id == session_id)
            if not include_archived:
                stmt = stmt.where(_MessageRow.archived == 0)
            stmt = stmt.order_by(_MessageRow.id)
            rows = s.scalars(stmt).all()
            return [self._row_to_dto(r) for r in rows]

    def add(self, *, session_id: str, message_id: str, role: str, text: str,
            ts: str, content_blocks: list[dict[str, Any]] | None = None,
            run_id: str | None = None,
            llm_attempt_id: str | None = None) -> Message:
        with self._factory.session() as s:
            row = _MessageRow(
                session_id=session_id,
                message_id=message_id,
                role=role,
                text=text,
                ts=ts,
                content_blocks=content_blocks,
                run_id=run_id,
                llm_attempt_id=llm_attempt_id,
            )
            s.add(row)
            s.commit()
            s.refresh(row)
        return self._row_to_dto(row)

    def archive(self, *, message_id: int) -> None:
        with self._factory.session() as s:
            row = s.scalar(select(_MessageRow).where(_MessageRow.id == message_id))
            if row is None:
                return
            row.archived = 1
            s.commit()


__all__ = [
    "Session",
    "Message",
    "SessionBook",
    "MessageBook",
    "_SessionRow",
    "_MessageRow",
]