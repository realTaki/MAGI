"""Semantic Firmware commands for the ConversationBook."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from ...base.BaseJob import BaseJob, BaseJobResult, BaseJobRow, JobStatus
from ...base.operateBookJob import OperateBookJobBoard
from ...base.time import utcnow
from ..books.conversationBook import Conversation


@dataclass
class CreateConversationJob(BaseJob):
    delivery_address: str 
    channel: str  
    topic: str  
    instruction: str | None = None
    info: str | None = None


@dataclass
class CreateConversationResult(BaseJobResult):
    conversation_id: int | None = None


class CreateConversationJobRow(BaseJobRow):
    __tablename__ = "jobs_create_conversation"

    delivery_address: Mapped[str] = mapped_column(Text, nullable=False)
    channel: Mapped[str] = mapped_column(Text, nullable=False)
    topic: Mapped[str] = mapped_column(Text, nullable=False, default="")
    instruction: Mapped[str | None] = mapped_column(Text, nullable=True)
    info: Mapped[str | None] = mapped_column(Text, nullable=True)
    conversation_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class CreateConversationJobBoard(
    OperateBookJobBoard[CreateConversationJob, CreateConversationResult, CreateConversationJobRow]
):
    job_cls = CreateConversationJob
    result_cls = CreateConversationResult
    row_cls = CreateConversationJobRow

    def _execute(self, job: CreateConversationJob) -> CreateConversationResult:
        conversation_id = self._book.add(
            Conversation(
                delivery_address=job.delivery_address,
                channel=job.channel,
                topic=job.topic,
                instruction=job.instruction,
                info=job.info,
            )
        )
        return CreateConversationResult(conversation_id=conversation_id)


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
        conversation = self._book.get(job.conversation_id)
        if conversation is None:
            return UpdateConversationSummaryResult(
                status=JobStatus.FAILED, error=f"conversation {job.conversation_id} does not exist"
            )
        conversation.summary = job.summary
        conversation.last_compaction_at = utcnow()
        self._book.update(conversation)
        return UpdateConversationSummaryResult()
