"""Semantic Firmware commands for the ConversationBook."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import JSON, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from ...base.BaseJob import BaseJob, BaseJobResult, BaseJobRow
from ...base.operateBookJob import OperateBookJobBoard
from ...base.time import utcnow
from ..books.conversationBook import Conversation


@dataclass
class GetConversationJob(BaseJob):
    """Read one conversation by MAGI id."""

    conversation_id: int


@dataclass
class GetConversationResult(BaseJobResult):
    conversation: Conversation | None = None


class GetConversationJobRow(BaseJobRow):
    __tablename__ = "jobs_get_conversation"

    conversation_id: Mapped[int] = mapped_column(Integer, nullable=False)
    conversation: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


class GetConversationJobBoard(
    OperateBookJobBoard[GetConversationJob, GetConversationResult, GetConversationJobRow]
):
    job_cls = GetConversationJob
    result_cls = GetConversationResult
    row_cls = GetConversationJobRow

    def _execute(self, job: GetConversationJob) -> GetConversationResult:
        return GetConversationResult(conversation=self._book.get(job.conversation_id))


@dataclass
class GetConversationForChannelJob(BaseJob):
    """Read the conversation for one channel endpoint."""

    channel: str
    delivery_address: str


class GetConversationForChannelJobRow(BaseJobRow):
    __tablename__ = "jobs_get_conversation_for_channel"

    channel: Mapped[str] = mapped_column(Text, nullable=False, default="")
    delivery_address: Mapped[str] = mapped_column(Text, nullable=False, default="")
    conversation: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


class GetConversationForChannelJobBoard(
    OperateBookJobBoard[
        GetConversationForChannelJob,
        GetConversationResult,
        GetConversationForChannelJobRow,
    ]
):
    job_cls = GetConversationForChannelJob
    result_cls = GetConversationResult
    row_cls = GetConversationForChannelJobRow

    def _execute(self, job: GetConversationForChannelJob) -> GetConversationResult:
        channel = job.channel.strip()
        delivery_address = job.delivery_address.strip()
        found = (
            self._book.list(channel=channel, delivery_address=delivery_address)
            if channel and delivery_address
            else []
        )
        return GetConversationResult(conversation=found[0] if found else None)


@dataclass
class UpdateConversationSummaryJob(BaseJob):
    conversation_id: int
    summary: str


@dataclass
class UpdateConversationSummaryResult(BaseJobResult):
    pass


class UpdateConversationSummaryJobRow(BaseJobRow):
    __tablename__ = "jobs_update_conversation_summary"

    conversation_id: Mapped[int] = mapped_column(Integer, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")


class UpdateConversationSummaryJobBoard(
    OperateBookJobBoard[
        UpdateConversationSummaryJob,
        UpdateConversationSummaryResult,
        UpdateConversationSummaryJobRow,
    ]
):
    job_cls = UpdateConversationSummaryJob
    result_cls = UpdateConversationSummaryResult
    row_cls = UpdateConversationSummaryJobRow

    def _execute(self, job: UpdateConversationSummaryJob) -> UpdateConversationSummaryResult:
        self._book.update(
            Conversation(
                id=job.conversation_id,
                summary=job.summary,
                last_compaction_at=utcnow(),
            )
        )
        return UpdateConversationSummaryResult()
