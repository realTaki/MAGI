"""Semantic Firmware commands for ContactBook."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import JSON, Integer, Text, delete, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from ...base.BaseJob import BaseJob, BaseJobResult, BaseJobRow, JobStatus
from ...base.operateBookJob import OperateBookJobBoard
from ...base.time import utcnow
from ..books.contactBook import Contact, ContactRole, ContactRow
from ..books.contactNoteBook import ContactNoteRow


def _valid_name(name: str) -> bool:
    return isinstance(name, str) and bool(name.strip())


@dataclass
class CreateContactJob(BaseJob):
    name: str = ""
    display_name: str | None = None
    role: ContactRole = ContactRole.GUEST


@dataclass
class CreateContactResult(BaseJobResult):
    contact_id: int | None = None


class CreateContactJobRow(BaseJobRow):
    __tablename__ = "jobs_create_contact"

    name: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    contact_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class CreateContactJobBoard(
    OperateBookJobBoard[CreateContactJob, CreateContactResult, CreateContactJobRow]
):
    job_cls = CreateContactJob
    result_cls = CreateContactResult
    row_cls = CreateContactJobRow

    def _execute(self, session: Session, job: CreateContactJob) -> CreateContactResult:
        if not _valid_name(job.name):
            return CreateContactResult(status=JobStatus.FAILED, error="contact name must be non-empty")
        row = ContactRow(
            name=job.name.strip(),
            display_name=job.display_name,
            role=job.role.value,
            last_seen_at=utcnow(),
        )
        session.add(row)
        session.flush()
        return CreateContactResult(contact_id=row.id)


@dataclass
class GetContactJob(BaseJob):
    contact_id: int = 0


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
    def _execute(self, session: Session, job: GetContactJob) -> GetContactResult:
        row = session.get(ContactRow, job.contact_id)
        return GetContactResult(contact=None if row is None else Contact.from_row(row))


@dataclass
class ListContactsJob(BaseJob):
    role: ContactRole | None = None


@dataclass
class ListContactsResult(BaseJobResult):
    contacts: list[Contact] = field(default_factory=list)


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
    def _execute(self, session: Session, job: ListContactsJob) -> ListContactsResult:
        stmt = select(ContactRow).order_by(ContactRow.id)
        if job.role is not None:
            stmt = stmt.where(ContactRow.role == job.role.value)
        return ListContactsResult(contacts=[Contact.from_row(row) for row in session.scalars(stmt)])


@dataclass
class UpdateContactJob(BaseJob):
    """Replace one Contact's mutable profile fields."""

    contact_id: int = 0
    name: str = ""
    display_name: str | None = None
    role: ContactRole = ContactRole.GUEST


@dataclass
class UpdateContactResult(BaseJobResult):
    pass


class UpdateContactJobRow(BaseJobRow):
    __tablename__ = "jobs_update_contact"

    contact_id: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    role: Mapped[str] = mapped_column(Text, nullable=False)


class UpdateContactJobBoard(
    OperateBookJobBoard[UpdateContactJob, UpdateContactResult, UpdateContactJobRow]
):
    job_cls = UpdateContactJob
    result_cls = UpdateContactResult
    row_cls = UpdateContactJobRow

    def _execute(self, session: Session, job: UpdateContactJob) -> UpdateContactResult:
        row = session.get(ContactRow, job.contact_id)
        if row is None:
            return UpdateContactResult(
                status=JobStatus.FAILED, error=f"contact {job.contact_id} does not exist"
            )
        if not _valid_name(job.name):
            return UpdateContactResult(status=JobStatus.FAILED, error="contact name must be non-empty")
        row.name = job.name.strip()
        row.display_name = job.display_name
        row.role = job.role.value
        return UpdateContactResult()


@dataclass
class TouchContactJob(BaseJob):
    contact_id: int = 0


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

    def _execute(self, session: Session, job: TouchContactJob) -> TouchContactResult:
        row = session.get(ContactRow, job.contact_id)
        if row is None:
            return TouchContactResult(
                status=JobStatus.FAILED, error=f"contact {job.contact_id} does not exist"
            )
        row.last_seen_at = utcnow()
        return TouchContactResult()


@dataclass
class DeleteContactJob(BaseJob):
    contact_id: int = 0


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

    def _execute(self, session: Session, job: DeleteContactJob) -> DeleteContactResult:
        row = session.get(ContactRow, job.contact_id)
        if row is None:
            return DeleteContactResult(
                status=JobStatus.FAILED, error=f"contact {job.contact_id} does not exist"
            )
        session.execute(delete(ContactNoteRow).where(ContactNoteRow.contact_id == job.contact_id))
        session.delete(row)
        return DeleteContactResult()
