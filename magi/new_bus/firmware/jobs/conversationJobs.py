"""Semantic Firmware commands for the ConversationBook."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Self

from sqlalchemy import JSON, Integer, Text
from sqlalchemy.orm import Mapped, Session, mapped_column

from ...base.BaseJob import BaseJob, BaseJobResult, BaseJobRow, JobStatus
from ...base.operateBookJob import OperateBookJobBoard
from ...base.time import utcnow
from ..books.conversationBook import Conversation, ConversationRow


@dataclass
class CreateConversationJob(BaseJob):
    delivery_address: str = ""
    contact_id: int = 0
    channel: str = ""
    title: str = ""


@dataclass
class CreateConversationResult(BaseJobResult):
    conversation: Conversation | None = None

    @classmethod
    def parse(cls, data: Mapping[str, Any]) -> Self:
        result = super().parse(data)
        raw = data.get("conversation")
        if isinstance(raw, dict):
            result.conversation = Conversation.parse(raw)
        return result


class CreateConversationJobRow(BaseJobRow):
    __tablename__ = "jobs_create_conversation"

    delivery_address: Mapped[str] = mapped_column(Text, nullable=False)
    contact_id: Mapped[int] = mapped_column(Integer, nullable=False)
    channel: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False, default="")
    conversation: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


class CreateConversationJobBoard(
    OperateBookJobBoard[CreateConversationJob, CreateConversationResult, CreateConversationJobRow]
):
    job_cls = CreateConversationJob
    result_cls = CreateConversationResult
    row_cls = CreateConversationJobRow

    def _execute(self, session: Session, job: CreateConversationJob) -> CreateConversationResult:
        if not job.delivery_address.strip():
            return CreateConversationResult(
                status=JobStatus.FAILED, error="delivery_address is required"
            )
        if job.contact_id < 0:
            return CreateConversationResult(
                status=JobStatus.FAILED, error="contact_id must be non-negative"
            )
        if not job.channel.strip():
            return CreateConversationResult(status=JobStatus.FAILED, error="channel is required")
        row = ConversationRow(
            delivery_address=job.delivery_address,
            contact_id=job.contact_id,
            channel=job.channel,
            title=job.title,
        )
        session.add(row)
        session.flush()
        return CreateConversationResult(conversation=Conversation.from_row(row))


@dataclass
class UpdateConversationSummaryJob(BaseJob):
    conversation_id: int = 0
    summary: str = ""


@dataclass
class UpdateConversationSummaryResult(BaseJobResult):
    conversation: Conversation | None = None

    @classmethod
    def parse(cls, data: Mapping[str, Any]) -> Self:
        result = super().parse(data)
        raw = data.get("conversation")
        if isinstance(raw, dict):
            result.conversation = Conversation.parse(raw)
        return result


class UpdateConversationSummaryJobRow(BaseJobRow):
    __tablename__ = "jobs_update_conversation_summary"

    conversation_id: Mapped[int] = mapped_column(Integer, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    conversation: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


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

    def _execute(
        self, session: Session, job: UpdateConversationSummaryJob
    ) -> UpdateConversationSummaryResult:
        row = session.get(ConversationRow, job.conversation_id)
        if row is None:
            return UpdateConversationSummaryResult(
                status=JobStatus.FAILED, error=f"conversation {job.conversation_id} does not exist"
            )
        row.summary = job.summary
        row.last_compaction_at = utcnow()
        session.flush()
        return UpdateConversationSummaryResult(conversation=Conversation.from_row(row))
