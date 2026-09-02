"""Semantic Firmware commands for the MessageBook."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import JSON, Boolean, Integer
from sqlalchemy.orm import Mapped, mapped_column

from ...base.BaseJob import BaseJob, BaseJobResult, BaseJobRow
from ...base.operateBookJob import OperateBookJobBoard
from ..books.messageBook import Message


@dataclass
class ListConversationMessagesJob(BaseJob):
    conversation_id: int  
    include_archived: bool = False
    last_n: int | None = None


@dataclass
class ListConversationMessagesResult(BaseJobResult):
    messages: list[Message]  | None = None


class ListConversationMessagesJobRow(BaseJobRow):
    __tablename__ = "jobs_list_conversation_messages"

    conversation_id: Mapped[int] = mapped_column(Integer, nullable=False)
    include_archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_n: Mapped[int | None] = mapped_column(Integer, nullable=True)
    messages: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)


class ListConversationMessagesJobBoard(
    OperateBookJobBoard[
        ListConversationMessagesJob, ListConversationMessagesResult, ListConversationMessagesJobRow
    ]
):
    job_cls = ListConversationMessagesJob
    result_cls = ListConversationMessagesResult
    row_cls = ListConversationMessagesJobRow
    def _execute(self, job: ListConversationMessagesJob) -> ListConversationMessagesResult:
        return ListConversationMessagesResult(
            messages=self._book.list(
                conversation_id=job.conversation_id,
                archived=None if job.include_archived else False,
                last_n=job.last_n,
            )
        )


@dataclass
class ArchiveMessagesJob(BaseJob):
    conversation_id: int  
    before_message_id: int  


@dataclass
class ArchiveMessagesResult(BaseJobResult):
    archived_count: int = 0


class ArchiveMessagesJobRow(BaseJobRow):
    __tablename__ = "jobs_archive_messages"

    conversation_id: Mapped[int] = mapped_column(Integer, nullable=False)
    before_message_id: Mapped[int] = mapped_column(Integer, nullable=False)
    archived_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class ArchiveMessagesJobBoard(
    OperateBookJobBoard[ArchiveMessagesJob, ArchiveMessagesResult, ArchiveMessagesJobRow]
):
    job_cls = ArchiveMessagesJob
    result_cls = ArchiveMessagesResult
    row_cls = ArchiveMessagesJobRow

    def _execute(self, job: ArchiveMessagesJob) -> ArchiveMessagesResult:
        archived = 0
        for message in self._book.list(conversation_id=job.conversation_id, archived=False):
            if message.id >= job.before_message_id:
                continue
            message.archived = True
            self._book.update(message)
            archived += 1
        return ArchiveMessagesResult(archived_count=archived)
