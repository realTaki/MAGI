"""Claimable agent-turn work for the agent Worker."""

from __future__ import annotations

from dataclasses import dataclass, replace

from sqlalchemy import Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from ...base.BaseJob import BaseJob, BaseJobBoard, BaseJobResult, BaseJobRow, JobStatus
from ...base.engine import EngineFactory
from ...base.go import go
from ...base.time import utcnow
from ..books.contactBook import ContactRow
from ..books.conversationBook import ConversationRow
from ..books.messageBook import MessageBook, MessageRow


@dataclass
class ChatNotify(BaseJob):
    """One inbound agent turn.

    Channels, tasks, and steering republish this envelope. ``text`` is
    the inbound body; ``conversation_id`` is the session it belongs to.
    ``contact_id`` is the speaker; ``0`` is the system contact.
    Publish writes the same row into MessageBook before the Job is claimable.
    """

    conversation_id: int
    text: str
    contact_id: int = 0


@dataclass
class ChatNotifyResult(BaseJobResult):
    """Terminal state of a turn. Failures use ``status`` and ``error``."""


class ChatNotifyRow(BaseJobRow):
    __tablename__ = "jobs_chat_notify"

    contact_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    conversation_id: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False, default="")


class ChatNotifyBoard(BaseJobBoard[ChatNotify, ChatNotifyResult, ChatNotifyRow]):
    job_cls = ChatNotify
    result_cls = ChatNotifyResult
    row_cls = ChatNotifyRow

    def __init__(self, factory: EngineFactory, *, book: MessageBook) -> None:
        super().__init__(factory)
        self._messages = book

    def publish(self, job: ChatNotify) -> int:
        job_id = self._publish(job)
        published = replace(job, id=job_id)
        error = None
        with self._messages._session() as session:
            if session.get(ConversationRow, published.conversation_id) is None:
                error = f"conversation {published.conversation_id} does not exist"
            elif session.get(ContactRow, published.contact_id) is None:
                error = f"contact {published.contact_id} does not exist"
            else:
                session.add(
                    MessageRow(
                        conversation_id=published.conversation_id,
                        contact_id=published.contact_id,
                        content=published.text,
                        timestamp=utcnow(),
                        archived=False,
                    )
                )
                session.commit()
        if error is not None:
            with self._session() as session:
                row = session.get_one(type(self).row_cls, job_id)
                row.status = JobStatus.FAILED.value
                row.error = error
                session.commit()
            return job_id
        go(self._post_publish(published))
        return job_id
