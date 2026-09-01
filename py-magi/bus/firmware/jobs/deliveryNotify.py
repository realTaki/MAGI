"""Claimable outbound-delivery work for channel Workers."""

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
        error = None
        with self._messages._session() as session:
            if session.get(ConversationRow, published.conversation_id) is None:
                error = f"conversation {published.conversation_id} does not exist"
            elif session.get(ContactRow, 1) is None:
                error = "contact 1 does not exist"
            else:
                session.add(
                    MessageRow(
                        conversation_id=published.conversation_id,
                        contact_id=1,
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
