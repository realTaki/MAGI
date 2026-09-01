"""Semantic Firmware commands for ContactBook."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import JSON, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from ...base.BaseJob import BaseJob, BaseJobResult, BaseJobRow, JobStatus
from ...base.operateBookJob import OperateBookJobBoard
from ...base.time import utcnow
from ..books.contactBook import Contact, ContactRole


def _valid_name(name: str) -> bool:
    return isinstance(name, str) and bool(name.strip())


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
        if not _valid_name(job.name):
            return CreateContactResult(status=JobStatus.FAILED, error="contact name must be non-empty")
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
        if job.role is None:
            return ListContactsResult(contacts=self._book.list())
        return ListContactsResult(contacts=self._book.list(role=job.role.value))


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
        contact = self._book.get(job.contact_id)
        if contact is None:
            return UpdateContactResult(
                status=JobStatus.FAILED, error=f"contact {job.contact_id} does not exist"
            )
        if job.name is not None:
            if not _valid_name(job.name):
                return UpdateContactResult(status=JobStatus.FAILED, error="contact name must be non-empty")
            contact.name = job.name.strip()
        if job.nickname is not None:
            contact.nickname = job.nickname
        if job.role is not None:
            contact.role = job.role
        self._book.update(contact)
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
        contact = self._book.get(job.contact_id)
        if contact is None:
            return TouchContactResult(
                status=JobStatus.FAILED, error=f"contact {job.contact_id} does not exist"
            )
        contact.last_seen_at = utcnow()
        self._book.update(contact)
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
        if not self._book.delete(job.contact_id):
            return DeleteContactResult(
                status=JobStatus.FAILED, error=f"contact {job.contact_id} does not exist"
            )
        return DeleteContactResult()
