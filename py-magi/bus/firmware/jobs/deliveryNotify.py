"""Claimable outbound-delivery work for channel Workers."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from ...base.BaseJob import BaseJob, BaseJobBoard, BaseJobResult, BaseJobRow


@dataclass
class DeliveryNotify(BaseJob):
    """One outbound reply to deliver.

    ``text`` is the body; ``conversation_id`` is the session it belongs
    to. Channel and delivery address live on the Conversation row, not
    on this notify.
    """

    conversation_id: int | None = None
    text: str = ""


@dataclass
class DeliveryNotifyResult(BaseJobResult):
    """Channel acknowledgement. Failures use ``status`` and ``error``."""


class DeliveryNotifyRow(BaseJobRow):
    __tablename__ = "jobs_delivery_notify"

    conversation_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    text: Mapped[str] = mapped_column(Text, nullable=False, default="")


class DeliveryNotifyBoard(
    BaseJobBoard[DeliveryNotify, DeliveryNotifyResult, DeliveryNotifyRow]
):
    job_cls = DeliveryNotify
    result_cls = DeliveryNotifyResult
    row_cls = DeliveryNotifyRow
