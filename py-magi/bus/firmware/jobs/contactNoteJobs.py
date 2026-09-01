"""Semantic Firmware commands for ContactNoteBook."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import JSON, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from ...base.BaseJob import BaseJob, BaseJobResult, BaseJobRow
from ...base.operateBookJob import OperateBookJobBoard
from ..books.contactNoteBook import ContactNote, NoteKind


@dataclass
class CreateContactNoteJob(BaseJob):
    contact_id: int 
    note: str = "nothing to say"
    kind: NoteKind = NoteKind.DAILY


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

    def _execute(self, job: CreateContactNoteJob) -> CreateContactNoteResult:
        note_id = self._book.add(
            ContactNote(contact_id=job.contact_id, note=job.note, kind=job.kind)
        )
        return CreateContactNoteResult(contact_note_id=note_id)


@dataclass
class GetContactNoteJob(BaseJob):
    contact_note_id: int 


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
    def _execute(self, job: GetContactNoteJob) -> GetContactNoteResult:
        return GetContactNoteResult(contact_note=self._book.get(job.contact_note_id))


@dataclass
class ListContactNotesJob(BaseJob):
    contact_id: int 
    kind: NoteKind | None = None


@dataclass
class ListContactNotesResult(BaseJobResult):
    contact_notes: list[ContactNote] | None = None


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
    def _execute(self, job: ListContactNotesJob) -> ListContactNotesResult:
        notes = self._book.list(
            contact_id=job.contact_id,
            kind=None if job.kind is None else job.kind.value,
        )
        return ListContactNotesResult(contact_notes=list(reversed(notes)))


@dataclass
class UpdateContactNoteJob(BaseJob):
    """Replace one ContactNote's text and classification."""

    contact_note_id: int
    note: str | None = None
    kind: NoteKind | None = None


@dataclass
class UpdateContactNoteResult(BaseJobResult):
    pass


class UpdateContactNoteJobRow(BaseJobRow):
    __tablename__ = "jobs_update_contact_note"

    contact_note_id: Mapped[int] = mapped_column(Integer, nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    kind: Mapped[str | None] = mapped_column(Text, nullable=True)


class UpdateContactNoteJobBoard(
    OperateBookJobBoard[UpdateContactNoteJob, UpdateContactNoteResult, UpdateContactNoteJobRow]
):
    job_cls = UpdateContactNoteJob
    result_cls = UpdateContactNoteResult
    row_cls = UpdateContactNoteJobRow

    def _execute(self, job: UpdateContactNoteJob) -> UpdateContactNoteResult:
        self._book.update(
            ContactNote(
                id=job.contact_note_id,
                note=job.note,
                kind=job.kind,
            )
        )
        return UpdateContactNoteResult()


@dataclass
class DeleteContactNoteJob(BaseJob):
    contact_note_id: int 


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

    def _execute(self, job: DeleteContactNoteJob) -> DeleteContactNoteResult:
        self._book.delete(job.contact_note_id)
        return DeleteContactNoteResult()
