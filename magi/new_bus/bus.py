"""Public BUS surface. External components never receive a BaseBook."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .base.backends import Backend
from .base.BaseBook import BaseBook
from .base.BaseJob import BaseJob, BaseJobBoard, BaseJobResult, JobStatus
from .base.errors import InvalidJobError
from .base.manageBookJob import ManageBookJob, ManageBookJobBoard
from .base.slot import Handler, Slot, SlotSpace


class Bus:
    """Logical backplane: publish / claim / attach / detach plus job queries."""

    def __init__(self, backend: Backend) -> None:
        self._backend = backend
        self._backend.ensure()
        self._slots = SlotSpace()
        self._books: dict[str, BaseBook] = {}
        self._book_boards: dict[str, ManageBookJobBoard] = {}
        self._job_boards: dict[type[BaseJob], BaseJobBoard] = {}
        from .firmware import attach

        attach(self)

    @property
    def books(self) -> tuple[str, ...]:
        return tuple(self._books)

    @property
    def jobs(self) -> tuple[type[BaseJob], ...]:
        return tuple(self._job_boards)

    def record_type(self, book: str) -> type:
        """Return the record dataclass that lists a BaseBook's fields."""
        try:
            item = self._books[book]
        except KeyError:
            raise InvalidJobError(f"book {book!r} is not provided by this BUS") from None
        record_cls = type(item).record_cls
        if record_cls is None:
            raise InvalidJobError(f"book {book!r} has no record type")
        return record_cls

    def mount_book(
        self,
        name: str,
        *,
        book_cls: type[BaseBook] = BaseBook,
        job_type: type[ManageBookJob] = ManageBookJob,
    ) -> None:
        if name in self._books:
            raise InvalidJobError(f"book {name!r} is already mounted")
        if not issubclass(job_type, ManageBookJob):
            raise InvalidJobError("mount_book job_type must be a ManageBookJob")
        book = book_cls(name, self._backend)
        self._books[name] = book
        self._book_boards[name] = ManageBookJobBoard(
            book, self._backend, self._slots, job_type=job_type
        )

    def mount_job(
        self,
        job_type: type[BaseJob],
        *,
        board_cls: type[BaseJobBoard] = BaseJobBoard,
    ) -> BaseJobBoard:
        if issubclass(job_type, ManageBookJob):
            raise InvalidJobError("ManageBookJob is mounted via mount_book, not mount_job")
        if job_type in self._job_boards:
            raise InvalidJobError(f"{job_type.type_name()} is already mounted")
        board = board_cls(job_type, self._backend, self._slots)
        self._job_boards[job_type] = board
        return board

    def job_board(self, job_type: type[BaseJob]) -> BaseJobBoard:
        """Return the mounted BaseJobBoard for a work BaseJob type."""
        return self._job_board(job_type)

    def publish(self, job: BaseJob) -> BaseJob:
        if isinstance(job, ManageBookJob):
            return self._book_board(job.book).publish(job)
        return self._job_board(type(job)).publish(job)

    def claim(self, job_type: type[BaseJob]) -> BaseJob | None:
        if issubclass(job_type, ManageBookJob):
            raise InvalidJobError("book jobs are executed by BUS and cannot be claimed")
        return self._job_board(job_type).claim()

    def complete(
        self, job: BaseJob, result: BaseJobResult | Mapping[str, Any] | None = None
    ) -> BaseJob:
        if isinstance(job, ManageBookJob):
            raise InvalidJobError("book jobs complete themselves")
        return self._job_board(type(job)).complete(job.id, result)

    def fail(self, job: BaseJob, error: str) -> BaseJob:
        if isinstance(job, ManageBookJob):
            raise InvalidJobError("book jobs fail themselves")
        return self._job_board(type(job)).fail(job.id, error)

    def get(self, job_type: type[BaseJob], job_id: int, *, book: str | None = None) -> BaseJob:
        if issubclass(job_type, ManageBookJob):
            return self._book_board(_book_name(job_type, book)).get(job_id)
        return self._job_board(job_type).get(job_id)

    def list(
        self,
        job_type: type[BaseJob],
        *,
        status: JobStatus | None = None,
        book: str | None = None,
    ) -> list[BaseJob]:
        if issubclass(job_type, ManageBookJob):
            return list(self._book_board(_book_name(job_type, book)).list(status=status))
        return self._job_board(job_type).list(status=status)

    def attach(self, job_type: type[BaseJob], slot: Slot, handler: Handler) -> None:
        self._slots.attach(job_type, slot, handler)

    def detach(self, job_type: type[BaseJob], slot: Slot, handler: Handler) -> None:
        self._slots.detach(job_type, slot, handler)

    def close(self) -> None:
        self._backend.close()

    def __enter__(self) -> Bus:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _book_board(self, name: str) -> ManageBookJobBoard:
        try:
            return self._book_boards[name]
        except KeyError:
            raise InvalidJobError(f"book {name!r} is not provided by this BUS") from None

    def _job_board(self, job_type: type[BaseJob]) -> BaseJobBoard:
        try:
            return self._job_boards[job_type]
        except KeyError:
            raise InvalidJobError(f"{job_type.type_name()} is not mounted") from None


def _book_name(job_type: type[BaseJob], book: str | None) -> str:
    name = book or getattr(job_type, "BOOK", None)
    if not name:
        raise InvalidJobError("get/list of a ManageBookJob requires book=")
    return str(name)
