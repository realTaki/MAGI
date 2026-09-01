"""Claimable outbound-delivery work for channel Workers."""

from __future__ import annotations

from dataclasses import dataclass, replace

from sqlalchemy import Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from ...base.BaseJob import BaseJob, BaseJobBoard, BaseJobResult, BaseJobRow
from ...base.engine import EngineFactory
from ...base.go import go
from ..books.messageBook import Message, MessageBook


@dataclass
class DeliveryNotify(BaseJob):
    """One outbound reply to deliver.

    ``text`` is the body; ``conversation_id`` is the session it belongs
    to. Channel and delivery address live on the Conversation row, not
    on this notify. Publish writes MAGI's own message (contact id 1)
    into MessageBook before the Job is claimable.
    """

    conversation_id: int
    text: str


@dataclass
class DeliveryNotifyResult(BaseJobResult):
    """Channel acknowledgement. Failures use ``status`` and ``error``."""


class DeliveryNotifyRow(BaseJobRow):
    __tablename__ = "jobs_delivery_notify"

    conversation_id: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False, default="")


class DeliveryNotifyBoard(
    BaseJobBoard[DeliveryNotify, DeliveryNotifyResult, DeliveryNotifyRow]
):
    job_cls = DeliveryNotify
    result_cls = DeliveryNotifyResult
    row_cls = DeliveryNotifyRow

    def __init__(self, factory: EngineFactory, *, book: MessageBook) -> None:
        super().__init__(factory)
        self._messages = book

    def publish(self, job: DeliveryNotify) -> int:
        job_id = self._publish(job)
        published = replace(job, id=job_id)
        self._messages.add(
            Message(
                contact_id=1,
                content=published.text,
                conversation_id=published.conversation_id,
            )
        )
        go(self._post_publish(published))
        return job_id
