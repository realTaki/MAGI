"""Semantic Firmware commands for the MessageBook."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import JSON, Boolean, Integer, Text
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


_SEARCH_LIMIT = 20


@dataclass
class SearchMessagesResult(BaseJobResult):
    messages: list[Message] | None = None


@dataclass
class SearchConversationMessagesJob(BaseJob):
    conversation_id: int
    q: str
    limit: int = _SEARCH_LIMIT


class SearchConversationMessagesJobRow(BaseJobRow):
    __tablename__ = "jobs_search_conversation_messages"

    conversation_id: Mapped[int] = mapped_column(Integer, nullable=False)
    q: Mapped[str] = mapped_column(Text, nullable=False, default="")
    limit: Mapped[int] = mapped_column(Integer, nullable=False, default=_SEARCH_LIMIT)
    messages: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)


class SearchConversationMessagesJobBoard(
    OperateBookJobBoard[
        SearchConversationMessagesJob,
        SearchMessagesResult,
        SearchConversationMessagesJobRow,
    ]
):
    job_cls = SearchConversationMessagesJob
    result_cls = SearchMessagesResult
    row_cls = SearchConversationMessagesJobRow

    def _execute(self, job: SearchConversationMessagesJob) -> SearchMessagesResult:
        limit = _SEARCH_LIMIT if job.limit <= 0 else min(job.limit, _SEARCH_LIMIT)
        return SearchMessagesResult(
            messages=self._book.search_conversation(
                conversation_id=job.conversation_id,
                q=job.q,
                limit=limit,
            )
        )


@dataclass
class SearchContactMessagesJob(BaseJob):
    contact_id: int
    q: str
    limit: int = _SEARCH_LIMIT


class SearchContactMessagesJobRow(BaseJobRow):
    __tablename__ = "jobs_search_contact_messages"

    contact_id: Mapped[int] = mapped_column(Integer, nullable=False)
    q: Mapped[str] = mapped_column(Text, nullable=False, default="")
    limit: Mapped[int] = mapped_column(Integer, nullable=False, default=_SEARCH_LIMIT)
    messages: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)


class SearchContactMessagesJobBoard(
    OperateBookJobBoard[
        SearchContactMessagesJob,
        SearchMessagesResult,
        SearchContactMessagesJobRow,
    ]
):
    job_cls = SearchContactMessagesJob
    result_cls = SearchMessagesResult
    row_cls = SearchContactMessagesJobRow

    def _execute(self, job: SearchContactMessagesJob) -> SearchMessagesResult:
        limit = _SEARCH_LIMIT if job.limit <= 0 else min(job.limit, _SEARCH_LIMIT)
        return SearchMessagesResult(
            messages=self._book.search_contact(
                contact_id=job.contact_id,
                q=job.q,
                limit=limit,
            )
        )
