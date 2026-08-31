"""Claimable agent-turn work for the agent Worker."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from ...base.BaseJob import BaseJob, BaseJobBoard, BaseJobResult, BaseJobRow


@dataclass
class ChatNotify(BaseJob):
    """One inbound agent turn.

    Channels, tasks, and steering republish this envelope. ``text`` is
    the user message; ``conversation_id`` is the session it belongs to.
    Contact and channel live on the Conversation row, not on this notify.
    """

    conversation_id: int 
    text: str


@dataclass
class ChatNotifyResult(BaseJobResult):
    """Terminal state of a turn. Failures use ``status`` and ``error``."""


class ChatNotifyRow(BaseJobRow):
    __tablename__ = "jobs_chat_notify"

    conversation_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    text: Mapped[str] = mapped_column(Text, nullable=False, default="")


class ChatNotifyBoard(BaseJobBoard[ChatNotify, ChatNotifyResult, ChatNotifyRow]):
    job_cls = ChatNotify
    result_cls = ChatNotifyResult
    row_cls = ChatNotifyRow
