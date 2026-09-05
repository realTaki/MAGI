"""Claimable outbound-delivery work for channel Workers."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Integer, Text, select
from sqlalchemy.orm import Mapped, mapped_column

from ...base.BaseJob import BaseJob, BaseJobResult, BaseJobRow, JobStatus
from ...base.engine import EngineFactory
from ...base.hookableJobBoard import HookableJobBoard
from ..books.contactBook import MAGI_CONTACT_ID
from ..books.conversationBook import ConversationBook
from ..books.messageBook import Message, MessageBook


@dataclass
class DeliveryNotify(BaseJob):
    """One outbound reply to deliver.

    Publish with ``conversation_id`` and ``text``. The board fills
    ``channel`` and ``address`` from ConversationBook so a channel
    Worker can ``claim_for_channel``.
    """

    conversation_id: int 
    text: str
    channel: str | None = None
    address: str | None = None


@dataclass
class DeliveryNotifyResult(BaseJobResult):
    """Channel acknowledgement. Failures use ``status`` and ``error``."""


class DeliveryNotifyRow(BaseJobRow):
    __tablename__ = "jobs_delivery_notify"

    conversation_id: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    channel: Mapped[str] = mapped_column(Text, nullable=False, default="")
    address: Mapped[str] = mapped_column(Text, nullable=False, default="")


class DeliveryNotifyBoard(
    HookableJobBoard[DeliveryNotify, DeliveryNotifyResult, DeliveryNotifyRow]
):
    job_cls = DeliveryNotify
    result_cls = DeliveryNotifyResult
    row_cls = DeliveryNotifyRow

    def __init__(
        self,
        factory: EngineFactory,
        *,
        book: MessageBook,
        conversations: ConversationBook,
    ) -> None:
        super().__init__(factory)
        self._messages = book
        self._conversations = conversations

    def claim_for_channel(self, channel: str) -> DeliveryNotify | None:
        """Claim one pending delivery for *channel*."""
        channel = channel.strip()
        if not channel:
            return None
        row_cls = type(self).row_cls
        with self._session() as session:
            row = session.scalar(
                select(row_cls)
                .where(
                    row_cls.status == JobStatus.PENDING.value,
                    row_cls.channel == channel,
                )
                .order_by(row_cls.created_at, row_cls.id)
                .limit(1)
            )
            if row is None:
                return None
            row.status = JobStatus.CLAIMED.value
            session.commit()
            return DeliveryNotify.from_row(row)

    def _prepare(self, job: DeliveryNotify) -> None:
        conversation = self._conversations.get(job.conversation_id)
        channel = "" if conversation is None else (conversation.channel or "").strip()
        address = (
            "" if conversation is None else (conversation.delivery_address or "").strip()
        )
        job.channel = channel
        job.address = address
        with self._session() as session:
            row = session.get_one(type(self).row_cls, job.id)
            row.channel = channel
            row.address = address
            if not channel or not address:
                self._write_result(
                    row,
                    DeliveryNotifyResult(
                        id=job.id,
                        status=JobStatus.FAILED,
                        error="conversation has no channel address",
                    ),
                )
                session.commit()
                return
            session.commit()
        self._messages.add(
            Message(
                contact_id=MAGI_CONTACT_ID,
                content=job.text,
                conversation_id=job.conversation_id,
            )
        )
