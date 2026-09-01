"""Claimable agent-turn work for the agent Worker."""

from __future__ import annotations

from dataclasses import dataclass, replace

from sqlalchemy import Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from ...base.BaseJob import BaseJob, BaseJobBoard, BaseJobResult, BaseJobRow
from ...base.engine import EngineFactory
from ...base.go import go
from ..books.contactBook import SYSTEM_CONTACT_ID
from ..books.messageBook import Message, MessageBook


@dataclass
class ChatNotify(BaseJob):
    """One inbound agent turn.

    Channels and tasks publish this envelope. ``text`` is the inbound body;
    ``conversation_id`` is the session it belongs to.
    ``contact_id`` is the speaker; ``SYSTEM_CONTACT_ID`` is reserved.
    Publish writes the same row into MessageBook before the Job is claimable.
    """

    conversation_id: int
    text: str
    contact_id: int = SYSTEM_CONTACT_ID


@dataclass
class ChatNotifyResult(BaseJobResult):
    """Terminal state of a turn. Failures use ``status`` and ``error``."""


class ChatNotifyRow(BaseJobRow):
    __tablename__ = "jobs_chat_notify"

    contact_id: Mapped[int] = mapped_column(
        Integer, nullable=False, default=SYSTEM_CONTACT_ID
    )
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
        self._messages.add(
            Message(
                contact_id=published.contact_id,
                content=published.text,
                conversation_id=published.conversation_id,
            )
        )
        go(self._post_publish(published))
        return job_id
