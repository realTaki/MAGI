"""Semantic Firmware commands for the ConversationBook."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import JSON, Integer, Text
from sqlalchemy.orm import Mapped, Session, mapped_column

from ...base.BaseJob import BaseJob, BaseJobRow, JobStatus
from ...base.operateBookJob import BookRecordResult, OperateBookJobBoard
from ...base.time import utcnow
from ..books.conversationBook import Conversation, ConversationRow


@dataclass
class CreateConversationJob(BaseJob):
    delivery_address: str = ""
    contact_id: int = 0
    channel: str = ""
    title: str = ""


@dataclass
class CreateConversationResult(BookRecordResult[Conversation]):
    conversation: Conversation | None = None
    record_cls = Conversation
    record_field = "conversation"


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
class UpdateConversationSummaryResult(BookRecordResult[Conversation]):
    conversation: Conversation | None = None
    record_cls = Conversation
    record_field = "conversation"


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
