"""deliveryNotifyJobBoard — Agent 向 channel 发出的投递通知。

这是单向通知：Agent 发布后不等待也不消费其结果；channel worker 的
``submit_result`` 只确认该 channel 的实际投递结果。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from old_bus.bases.job import BaseJob, BaseJobBoard, BaseJobResult, BaseJobRowMixin

if TYPE_CHECKING:
    from old_bus.firmwares.books.local.conversationBook import MessageBook

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DeliveryNotifyJob(BaseJob):
    """One outbound channel notification, persisted before wire I/O."""

    channel: str
    text: str = ""
    conversation_id: int | None = None
    contact_id: int | None = None
    destination: str | None = None


@dataclass(frozen=True, slots=True)
class DeliveryNotifyResult(BaseJobResult):
    """Channel-owned delivery acknowledgement; Agent never waits for it."""


class _DeliveryNotifyJobRow(BaseJobRowMixin):
    __tablename__ = "delivery_notify_jobs"
    __table_args__ = {"extend_existing": True}

    channel: Mapped[str] = mapped_column(Text, nullable=False)
    text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    conversation_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    contact_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    destination: Mapped[str | None] = mapped_column(Text, nullable=True)


class deliveryNotifyJobBoard(BaseJobBoard[_DeliveryNotifyJobRow, DeliveryNotifyJob, DeliveryNotifyResult]):
    """Channel-scoped outbound notification queue.

    The delivery row is committed before the assistant transcript projection.
    Consequently a channel outage cannot erase the reply intent: the notify
    row keeps the original text and channel worker writes its own terminal
    acknowledgement later.
    """

    job_model = _DeliveryNotifyJobRow
    job_cls = DeliveryNotifyJob
    result_cls = DeliveryNotifyResult

    def __init__(self, factory, *, messages_book: MessageBook | None = None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(factory)
        self._messages_book = messages_book

    def publish(self, job: DeliveryNotifyJob) -> int:
        job_id = super().publish(job)
        if self._messages_book is not None:
            try:
                from old_bus.firmwares.books.local.conversationBook import (
                    AgentMessageRole,
                    Message,
                )

                self._messages_book.add(Message(
                    conversation_id=job.conversation_id or 0,
                    role=AgentMessageRole.ASSISTANT,
                    text=job.text,
                ))
            except Exception:
                logger.exception(
                    "deliveryNotifyJobBoard.publish: messages_book.add failed "
                    "(conversation=%s, channel=%s); delivery notify %s remains durable",
                    job.conversation_id,
                    job.channel,
                    job_id,
                )
        return job_id

    def claim_for_channel(
        self, *, channel: str, worker_id: str
    ) -> DeliveryNotifyJob | None:
        with self._session() as session:
            row = self._cas_claim(
                session,
                owner=self._require_worker_id(worker_id),
                extra_where=[_DeliveryNotifyJobRow.channel == channel],
            )
            session.commit()
            if row is None:
                return None
            fresh = session.get(_DeliveryNotifyJobRow, row.job_id)
            return self._map_row(fresh, self.job_cls) if fresh else None


__all__ = [
    "DeliveryNotifyJob",
    "DeliveryNotifyResult",
    "deliveryNotifyJobBoard",
    "_DeliveryNotifyJobRow",
]
