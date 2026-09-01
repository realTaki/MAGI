"""Semantic Firmware commands for ContactBook."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import JSON, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from ...base.BaseJob import BaseJob, BaseJobResult, BaseJobRow
from ...base.operateBookJob import OperateBookJobBoard
from ..books.contactBook import Contact, ContactRole


@dataclass
class CreateContactJob(BaseJob):
    name: str = "New Contact"
    nickname: str | None = None
    role: ContactRole = ContactRole.STRANGER


@dataclass
class CreateContactResult(BaseJobResult):
    contact_id: int | None = None


class CreateContactJobRow(BaseJobRow):
    __tablename__ = "jobs_create_contact"

    name: Mapped[str] = mapped_column(Text, nullable=False)
    nickname: Mapped[str | None] = mapped_column(Text, nullable=True)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    contact_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class CreateContactJobBoard(
    OperateBookJobBoard[CreateContactJob, CreateContactResult, CreateContactJobRow]
):
    job_cls = CreateContactJob
    result_cls = CreateContactResult
    row_cls = CreateContactJobRow

    def _execute(self, job: CreateContactJob) -> CreateContactResult:
        contact_id = self._book.add(
            Contact(name=job.name.strip(), nickname=job.nickname, role=job.role)
        )
        return CreateContactResult(contact_id=contact_id)


@dataclass
class GetContactJob(BaseJob):
    contact_id: int 


@dataclass
class GetContactResult(BaseJobResult):
    contact: Contact | None = None


class GetContactJobRow(BaseJobRow):
    __tablename__ = "jobs_get_contact"

    contact_id: Mapped[int] = mapped_column(Integer, nullable=False)
    contact: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


class GetContactJobBoard(OperateBookJobBoard[GetContactJob, GetContactResult, GetContactJobRow]):
    job_cls = GetContactJob
    result_cls = GetContactResult
    row_cls = GetContactJobRow
    def _execute(self, job: GetContactJob) -> GetContactResult:
        return GetContactResult(contact=self._book.get(job.contact_id))


@dataclass
class ListContactsJob(BaseJob):
    role: ContactRole | None = None


@dataclass
class ListContactsResult(BaseJobResult):
    contacts: list[Contact] | None = None


class ListContactsJobRow(BaseJobRow):
    __tablename__ = "jobs_list_contacts"

    role: Mapped[str | None] = mapped_column(Text, nullable=True)
    contacts: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)


class ListContactsJobBoard(
    OperateBookJobBoard[ListContactsJob, ListContactsResult, ListContactsJobRow]
):
    job_cls = ListContactsJob
    result_cls = ListContactsResult
    row_cls = ListContactsJobRow

    def _execute(self, job: ListContactsJob) -> ListContactsResult:
        return ListContactsResult(
            contacts=self._book.list(role=None if job.role is None else job.role.value)
        )


@dataclass
class UpdateContactJob(BaseJob):
    """Replace one Contact's mutable profile fields."""

    contact_id: int 
    name: str | None = None
    nickname: str | None = None
    role: ContactRole | None = None


@dataclass
class UpdateContactResult(BaseJobResult):
    pass


class UpdateContactJobRow(BaseJobRow):
    __tablename__ = "jobs_update_contact"

    contact_id: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str | None] = mapped_column(Text, nullable=True)
    nickname: Mapped[str | None] = mapped_column(Text, nullable=True)
    role: Mapped[str | None] = mapped_column(Text, nullable=True)


class UpdateContactJobBoard(
    OperateBookJobBoard[UpdateContactJob, UpdateContactResult, UpdateContactJobRow]
):
    job_cls = UpdateContactJob
    result_cls = UpdateContactResult
    row_cls = UpdateContactJobRow

    def _execute(self, job: UpdateContactJob) -> UpdateContactResult:
        self._book.update(
            Contact(
                id=job.contact_id,
                name=None if job.name is None else job.name.strip(),
                nickname=job.nickname,
                role=job.role,
            )
        )
        return UpdateContactResult()


@dataclass
class TouchContactJob(BaseJob):
    contact_id: int 


@dataclass
class TouchContactResult(BaseJobResult):
    pass


class TouchContactJobRow(BaseJobRow):
    __tablename__ = "jobs_touch_contact"

    contact_id: Mapped[int] = mapped_column(Integer, nullable=False)


class TouchContactJobBoard(
    OperateBookJobBoard[TouchContactJob, TouchContactResult, TouchContactJobRow]
):
    job_cls = TouchContactJob
    result_cls = TouchContactResult
    row_cls = TouchContactJobRow

    def _execute(self, job: TouchContactJob) -> TouchContactResult:
        self._book.update(Contact(id=job.contact_id))
        return TouchContactResult()


@dataclass
class DeleteContactJob(BaseJob):
    contact_id: int 


@dataclass
class DeleteContactResult(BaseJobResult):
    pass


class DeleteContactJobRow(BaseJobRow):
    __tablename__ = "jobs_delete_contact"

    contact_id: Mapped[int] = mapped_column(Integer, nullable=False)


class DeleteContactJobBoard(
    OperateBookJobBoard[DeleteContactJob, DeleteContactResult, DeleteContactJobRow]
):
    job_cls = DeleteContactJob
    result_cls = DeleteContactResult
    row_cls = DeleteContactJobRow

    def _execute(self, job: DeleteContactJob) -> DeleteContactResult:
        self._book.delete(job.contact_id)
        return DeleteContactResult()
