"""Semantic Firmware commands for the MessageBook."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Integer, Text, and_, select, update
from sqlalchemy.orm import Mapped, Session, mapped_column

from ...base.BaseJob import BaseJob, BaseJobResult, BaseJobRow, JobStatus
from ...base.operateBookJob import OperateBookJobBoard
from ...base.time import BaseTime, utcnow
from ..books.conversationBook import ConversationRow
from ..books.messageBook import Message, MessageRole, MessageRow


@dataclass
class AppendMessageJob(BaseJob):
    conversation_id: int = 0
    role: MessageRole = MessageRole.USER
    content: str = ""
    timestamp: BaseTime = field(default_factory=utcnow)


@dataclass
class AppendMessageResult(BaseJobResult):
    message_id: int | None = None


class AppendMessageJobRow(BaseJobRow):
    __tablename__ = "jobs_append_message"

    conversation_id: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    timestamp: Mapped[BaseTime] = mapped_column(DateTime, nullable=False)
    message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class AppendMessageJobBoard(
    OperateBookJobBoard[AppendMessageJob, AppendMessageResult, AppendMessageJobRow]
):
    job_cls = AppendMessageJob
    result_cls = AppendMessageResult
    row_cls = AppendMessageJobRow

    def _execute(self, session: Session, job: AppendMessageJob) -> AppendMessageResult:
        if session.get(ConversationRow, job.conversation_id) is None:
            return AppendMessageResult(
                status=JobStatus.FAILED, error=f"conversation {job.conversation_id} does not exist"
            )
        try:
            role = MessageRole(job.role)
        except ValueError:
            return AppendMessageResult(
                status=JobStatus.FAILED, error=f"unsupported message role {job.role!r}"
            )
        row = MessageRow(
            conversation_id=job.conversation_id,
            role=role.value,
            content=job.content,
            timestamp=job.timestamp,
            archived=False,
        )
        session.add(row)
        session.flush()
        return AppendMessageResult(message_id=row.id)


@dataclass
class ListConversationMessagesJob(BaseJob):
    conversation_id: int = 0
    include_archived: bool = False


@dataclass
class ListConversationMessagesResult(BaseJobResult):
    messages: list[Message] = field(default_factory=list)


class ListConversationMessagesJobRow(BaseJobRow):
    __tablename__ = "jobs_list_conversation_messages"

    conversation_id: Mapped[int] = mapped_column(Integer, nullable=False)
    include_archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    messages: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)


class ListConversationMessagesJobBoard(
    OperateBookJobBoard[
        ListConversationMessagesJob, ListConversationMessagesResult, ListConversationMessagesJobRow
    ]
):
    job_cls = ListConversationMessagesJob
    result_cls = ListConversationMessagesResult
    row_cls = ListConversationMessagesJobRow
    def _execute(
        self, session: Session, job: ListConversationMessagesJob
    ) -> ListConversationMessagesResult:
        stmt = select(MessageRow).where(MessageRow.conversation_id == job.conversation_id)
        if not job.include_archived:
            stmt = stmt.where(MessageRow.archived.is_(False))
        rows = list(session.scalars(stmt.order_by(MessageRow.id)))
        return ListConversationMessagesResult(messages=[Message.from_row(row) for row in rows])


@dataclass
class ArchiveMessagesJob(BaseJob):
    conversation_id: int = 0
    before_message_id: int | None = None


@dataclass
class ArchiveMessagesResult(BaseJobResult):
    archived_count: int = 0


class ArchiveMessagesJobRow(BaseJobRow):
    __tablename__ = "jobs_archive_messages"

    conversation_id: Mapped[int] = mapped_column(Integer, nullable=False)
    before_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    archived_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class ArchiveMessagesJobBoard(
    OperateBookJobBoard[ArchiveMessagesJob, ArchiveMessagesResult, ArchiveMessagesJobRow]
):
    job_cls = ArchiveMessagesJob
    result_cls = ArchiveMessagesResult
    row_cls = ArchiveMessagesJobRow

    def _execute(self, session: Session, job: ArchiveMessagesJob) -> ArchiveMessagesResult:
        conditions = [
            MessageRow.conversation_id == job.conversation_id,
            MessageRow.archived.is_(False),
        ]
        if job.before_message_id is not None:
            conditions.append(MessageRow.id < job.before_message_id)
        changed = session.execute(update(MessageRow).where(and_(*conditions)).values(archived=True))
        return ArchiveMessagesResult(archived_count=int(getattr(changed, "rowcount", 0) or 0))
