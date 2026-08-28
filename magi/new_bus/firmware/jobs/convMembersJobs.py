"""Semantic Firmware commands for :class:`ConvMembersBook`."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import JSON, Integer, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from ...base.BaseJob import BaseJob, BaseJobResult, BaseJobRow, JobStatus
from ...base.operateBookJob import OperateBookJobBoard
from ..books.contactBook import ContactRow
from ..books.conversationBook import ConversationRow
from ..books.convMembersBook import ConvMember, ConvMemberRow


@dataclass
class AddConversationMemberJob(BaseJob):
    conversation_id: int = 0
    contact_id: int = 0


@dataclass
class AddConversationMemberResult(BaseJobResult):
    pass


class AddConversationMemberJobRow(BaseJobRow):
    __tablename__ = "jobs_add_conversation_member"

    conversation_id: Mapped[int] = mapped_column(Integer, nullable=False)
    contact_id: Mapped[int] = mapped_column(Integer, nullable=False)


class AddConversationMemberJobBoard(
    OperateBookJobBoard[
        AddConversationMemberJob, AddConversationMemberResult, AddConversationMemberJobRow
    ]
):
    job_cls = AddConversationMemberJob
    result_cls = AddConversationMemberResult
    row_cls = AddConversationMemberJobRow

    def _execute(self, session: Session, job: AddConversationMemberJob) -> AddConversationMemberResult:
        conversation = session.get(ConversationRow, job.conversation_id)
        if conversation is None:
            return AddConversationMemberResult(
                status=JobStatus.FAILED, error=f"conversation {job.conversation_id} does not exist"
            )
        if session.get(ContactRow, job.contact_id) is None:
            return AddConversationMemberResult(
                status=JobStatus.FAILED, error=f"contact {job.contact_id} does not exist"
            )
        if conversation.owner_contact_id == job.contact_id:
            return AddConversationMemberResult(
                status=JobStatus.FAILED,
                error="conversation owner is stored on Conversation, not ConvMembersBook",
            )
        row = session.scalar(
            select(ConvMemberRow).where(
                ConvMemberRow.conversation_id == job.conversation_id,
                ConvMemberRow.contact_id == job.contact_id,
            )
        )
        if row is not None:
            return AddConversationMemberResult()
        row = ConvMemberRow(conversation_id=job.conversation_id, contact_id=job.contact_id)
        session.add(row)
        session.flush()
        return AddConversationMemberResult()


@dataclass
class ListConversationMembersJob(BaseJob):
    conversation_id: int = 0


@dataclass
class ListConversationMembersResult(BaseJobResult):
    members: list[ConvMember] = field(default_factory=list)


class ListConversationMembersJobRow(BaseJobRow):
    __tablename__ = "jobs_list_conversation_members"

    conversation_id: Mapped[int] = mapped_column(Integer, nullable=False)
    members: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)


class ListConversationMembersJobBoard(
    OperateBookJobBoard[
        ListConversationMembersJob, ListConversationMembersResult, ListConversationMembersJobRow
    ]
):
    job_cls = ListConversationMembersJob
    result_cls = ListConversationMembersResult
    row_cls = ListConversationMembersJobRow

    def _execute(
        self, session: Session, job: ListConversationMembersJob
    ) -> ListConversationMembersResult:
        rows = session.scalars(
            select(ConvMemberRow)
            .where(ConvMemberRow.conversation_id == job.conversation_id)
            .order_by(ConvMemberRow.id)
        )
        return ListConversationMembersResult(members=[ConvMember.from_row(row) for row in rows])


@dataclass
class RemoveConversationMemberJob(BaseJob):
    conversation_id: int = 0
    contact_id: int = 0


@dataclass
class RemoveConversationMemberResult(BaseJobResult):
    pass


class RemoveConversationMemberJobRow(BaseJobRow):
    __tablename__ = "jobs_remove_conversation_member"

    conversation_id: Mapped[int] = mapped_column(Integer, nullable=False)
    contact_id: Mapped[int] = mapped_column(Integer, nullable=False)


class RemoveConversationMemberJobBoard(
    OperateBookJobBoard[
        RemoveConversationMemberJob,
        RemoveConversationMemberResult,
        RemoveConversationMemberJobRow,
    ]
):
    job_cls = RemoveConversationMemberJob
    result_cls = RemoveConversationMemberResult
    row_cls = RemoveConversationMemberJobRow

    def _execute(
        self, session: Session, job: RemoveConversationMemberJob
    ) -> RemoveConversationMemberResult:
        row = session.scalar(
            select(ConvMemberRow).where(
                ConvMemberRow.conversation_id == job.conversation_id,
                ConvMemberRow.contact_id == job.contact_id,
            )
        )
        if row is None:
            return RemoveConversationMemberResult()
        session.delete(row)
        return RemoveConversationMemberResult()
