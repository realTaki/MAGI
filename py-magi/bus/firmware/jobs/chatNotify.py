"""Claimable agent-turn work for the agent Worker."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from ...base.BaseJob import BaseJob, BaseJobResult, BaseJobRow, JobStatus
from ...base.engine import EngineFactory
from ...base.hookableJobBoard import HookableJobBoard
from ..books.contactBook import SYSTEM_CONTACT_ID
from ..books.conversationBook import ConversationBook
from ..books.messageBook import Message, MessageBook


@dataclass
class ChatNotify(BaseJob):
    """One inbound agent turn.

    External channels publish ``channel`` + ``delivery_address``.
    Internal callers may publish ``conversation_id`` instead.
    If the endpoint is given without an id, the board fills
    ``conversation_id``. ``contact_id`` is the speaker;
    ``SYSTEM_CONTACT_ID`` is reserved.
    """

    text: str
    channel: str = ""
    delivery_address: str = ""
    contact_id: int = SYSTEM_CONTACT_ID
    conversation_id: int | None = None


@dataclass
class ChatNotifyResult(BaseJobResult):
    """Terminal state of a turn. Failures use ``status`` and ``error``."""


class ChatNotifyRow(BaseJobRow):
    __tablename__ = "jobs_chat_notify"

    contact_id: Mapped[int] = mapped_column(
        Integer, nullable=False, default=SYSTEM_CONTACT_ID
    )
    channel: Mapped[str] = mapped_column(Text, nullable=False, default="")
    delivery_address: Mapped[str] = mapped_column(Text, nullable=False, default="")
    conversation_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    text: Mapped[str] = mapped_column(Text, nullable=False, default="")


class ChatNotifyBoard(HookableJobBoard[ChatNotify, ChatNotifyResult, ChatNotifyRow]):
    job_cls = ChatNotify
    result_cls = ChatNotifyResult
    row_cls = ChatNotifyRow

    def __init__(
        self,
        factory: EngineFactory,
        *,
        messages: MessageBook,
        conversations: ConversationBook,
    ) -> None:
        super().__init__(factory)
        self._messages = messages
        self._conversations = conversations

    def _prepare(self, job: ChatNotify) -> None:
        conversation_id = job.conversation_id
        channel = job.channel.strip()
        delivery_address = job.delivery_address.strip()
        job.channel = channel
        job.delivery_address = delivery_address
        if not conversation_id:
            if not channel or not delivery_address:
                with self._session() as session:
                    row = session.get_one(type(self).row_cls, job.id)
                    self._write_result(
                        row,
                        ChatNotifyResult(
                            id=job.id,
                            status=JobStatus.FAILED,
                            error="ChatNotify requires conversation_id or channel and delivery_address",
                        ),
                    )
                    session.commit()
                return
            conversation_id = self._conversations.add_for_channel(
                channel=channel,
                delivery_address=delivery_address,
            )
            job.conversation_id = conversation_id
        with self._session() as session:
            row = session.get_one(type(self).row_cls, job.id)
            row.channel = channel
            row.delivery_address = delivery_address
            row.conversation_id = conversation_id
            session.commit()
        self._messages.add(
            Message(
                contact_id=job.contact_id,
                content=job.text,
                conversation_id=conversation_id,
            )
        )
