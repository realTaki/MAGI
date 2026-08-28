"""Semantic Firmware commands for ContactNoteBook."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import JSON, Integer, Text, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from ...base.BaseJob import BaseJob, BaseJobResult, BaseJobRow, JobStatus
from ...base.operateBookJob import OperateBookJobBoard
from ..books.contactBook import ContactRow
from ..books.contactNoteBook import ContactNote, ContactNoteRow, NoteKind


@dataclass
class CreateContactNoteJob(BaseJob):
    contact_id: int = 0
    note: str = ""
    kind: NoteKind = NoteKind.PERMANENT


@dataclass
class CreateContactNoteResult(BaseJobResult):
    contact_note_id: int | None = None


class CreateContactNoteJobRow(BaseJobRow):
    __tablename__ = "jobs_create_contact_note"

    contact_id: Mapped[int] = mapped_column(Integer, nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    contact_note_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class CreateContactNoteJobBoard(
    OperateBookJobBoard[CreateContactNoteJob, CreateContactNoteResult, CreateContactNoteJobRow]
):
    job_cls = CreateContactNoteJob
    result_cls = CreateContactNoteResult
    row_cls = CreateContactNoteJobRow

    def _execute(self, session: Session, job: CreateContactNoteJob) -> CreateContactNoteResult:
        if session.get(ContactRow, job.contact_id) is None:
            return CreateContactNoteResult(
                status=JobStatus.FAILED, error=f"contact {job.contact_id} does not exist"
            )
        if not job.note.strip():
            return CreateContactNoteResult(status=JobStatus.FAILED, error="contact note must be non-empty")
        row = ContactNoteRow(
            contact_id=job.contact_id,
            note=job.note,
            kind=job.kind.value,
        )
        session.add(row)
        session.flush()
        return CreateContactNoteResult(contact_note_id=row.id)


@dataclass
class GetContactNoteJob(BaseJob):
    contact_note_id: int = 0


@dataclass
class GetContactNoteResult(BaseJobResult):
    contact_note: ContactNote | None = None


class GetContactNoteJobRow(BaseJobRow):
    __tablename__ = "jobs_get_contact_note"

    contact_note_id: Mapped[int] = mapped_column(Integer, nullable=False)
    contact_note: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


class GetContactNoteJobBoard(
    OperateBookJobBoard[GetContactNoteJob, GetContactNoteResult, GetContactNoteJobRow]
):
    job_cls = GetContactNoteJob
    result_cls = GetContactNoteResult
    row_cls = GetContactNoteJobRow
    def _execute(self, session: Session, job: GetContactNoteJob) -> GetContactNoteResult:
        row = session.get(ContactNoteRow, job.contact_note_id)
        return GetContactNoteResult(contact_note=None if row is None else ContactNote.from_row(row))


@dataclass
class ListContactNotesJob(BaseJob):
    contact_id: int = 0
    kind: NoteKind | None = None


@dataclass
class ListContactNotesResult(BaseJobResult):
    contact_notes: list[ContactNote] = field(default_factory=list)


class ListContactNotesJobRow(BaseJobRow):
    __tablename__ = "jobs_list_contact_notes"

    contact_id: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str | None] = mapped_column(Text, nullable=True)
    contact_notes: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)


class ListContactNotesJobBoard(
    OperateBookJobBoard[ListContactNotesJob, ListContactNotesResult, ListContactNotesJobRow]
):
    job_cls = ListContactNotesJob
    result_cls = ListContactNotesResult
    row_cls = ListContactNotesJobRow
    def _execute(self, session: Session, job: ListContactNotesJob) -> ListContactNotesResult:
        stmt = select(ContactNoteRow).where(ContactNoteRow.contact_id == job.contact_id)
        if job.kind is not None:
            stmt = stmt.where(ContactNoteRow.kind == job.kind.value)
        rows = session.scalars(stmt.order_by(ContactNoteRow.id.desc()))
        return ListContactNotesResult(contact_notes=[ContactNote.from_row(row) for row in rows])


@dataclass
class UpdateContactNoteJob(BaseJob):
    """Replace one ContactNote's text and classification."""

    contact_note_id: int = 0
    note: str = ""
    kind: NoteKind = NoteKind.PERMANENT


@dataclass
class UpdateContactNoteResult(BaseJobResult):
    pass


class UpdateContactNoteJobRow(BaseJobRow):
    __tablename__ = "jobs_update_contact_note"

    contact_note_id: Mapped[int] = mapped_column(Integer, nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)


class UpdateContactNoteJobBoard(
    OperateBookJobBoard[UpdateContactNoteJob, UpdateContactNoteResult, UpdateContactNoteJobRow]
):
    job_cls = UpdateContactNoteJob
    result_cls = UpdateContactNoteResult
    row_cls = UpdateContactNoteJobRow

    def _execute(self, session: Session, job: UpdateContactNoteJob) -> UpdateContactNoteResult:
        row = session.get(ContactNoteRow, job.contact_note_id)
        if row is None:
            return UpdateContactNoteResult(
                status=JobStatus.FAILED, error=f"contact note {job.contact_note_id} does not exist"
            )
        if not job.note.strip():
            return UpdateContactNoteResult(status=JobStatus.FAILED, error="contact note must be non-empty")
        row.note = job.note
        row.kind = job.kind.value
        return UpdateContactNoteResult()


@dataclass
class DeleteContactNoteJob(BaseJob):
    contact_note_id: int = 0


@dataclass
class DeleteContactNoteResult(BaseJobResult):
    pass


class DeleteContactNoteJobRow(BaseJobRow):
    __tablename__ = "jobs_delete_contact_note"

    contact_note_id: Mapped[int] = mapped_column(Integer, nullable=False)


class DeleteContactNoteJobBoard(
    OperateBookJobBoard[DeleteContactNoteJob, DeleteContactNoteResult, DeleteContactNoteJobRow]
):
    job_cls = DeleteContactNoteJob
    result_cls = DeleteContactNoteResult
    row_cls = DeleteContactNoteJobRow

    def _execute(self, session: Session, job: DeleteContactNoteJob) -> DeleteContactNoteResult:
        row = session.get(ContactNoteRow, job.contact_note_id)
        if row is None:
            return DeleteContactNoteResult(
                status=JobStatus.FAILED, error=f"contact note {job.contact_note_id} does not exist"
            )
        session.delete(row)
        return DeleteContactNoteResult()
