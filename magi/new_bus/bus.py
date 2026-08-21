"""Public BUS surface. External components never receive a BaseBook."""

from __future__ import annotations

from typing import Any, cast, overload

from .base.BaseBook import BaseBook, BaseRecord
from .base.BaseFileBook import BaseFileBook
from .base.BaseJob import BaseJob, BaseJobBoard, BaseJobResult, JobStatus
from .base.engine import EngineFactory
from .base.errors import InvalidJobError
from .base.file import FileBackend
from .base.openBookJob import OpenBookJob, OpenBookJobBoard, OpenBookJobResult


class Bus:
    """Logical backplane: publish / claim plus job queries."""

    def __init__(self, factory: EngineFactory, *, files: FileBackend | None = None) -> None:
        if not isinstance(factory, EngineFactory):
            raise InvalidJobError("Bus requires EngineFactory")
        self._factory = factory
        self._files = files
        if self._files is not None:
            self._files.ensure()
        from .firmware.versions.schema import prepare_schema

        prepare_schema(self._factory)
        self._books: dict[str, BaseBook[Any] | BaseFileBook] = {}
        self._book_boards: dict[str, OpenBookJobBoard[Any]] = {}
        self._job_boards: dict[type[BaseJob], BaseJobBoard[Any, Any, Any]] = {}
        from .firmware import attach

        attach(self)

    @property
    def books(self) -> tuple[str, ...]:
        return tuple(self._books)

    @property
    def jobs(self) -> tuple[type[BaseJob], ...]:
        return tuple(self._job_boards)

    def record_type(self, book: str) -> type[BaseRecord]:
        """Return the record dataclass that lists a BaseBook's fields."""
        try:
            item = self._books[book]
        except KeyError:
            raise InvalidJobError(f"book {book!r} is not provided by this BUS") from None
        if not isinstance(item, BaseBook):
            raise InvalidJobError(f"book {book!r} has no record type")
        return item.record_cls

    def mount_book(self, mounted: type[OpenBookJobBoard] | type[BaseFileBook]) -> None:
        if issubclass(mounted, BaseFileBook):
            name = _book_key(mounted)
            if name in self._books:
                raise InvalidJobError(f"book {name!r} is already mounted")
            if self._files is None:
                raise InvalidJobError("BaseFileBook requires a FileBackend")
            self._books[name] = mounted(self._files)
            return
        board = mounted(self._factory)
        name = board.book.record_cls.__name__
        if name in self._books:
            raise InvalidJobError(f"book {name!r} is already mounted")
        self._books[name] = board.book
        self._book_boards[name] = board

    def mount_job[JobT: BaseJob](
        self,
        job_type: type[JobT],
        *,
        board_cls: type[BaseJobBoard[Any, Any, Any]] = BaseJobBoard,
    ) -> BaseJobBoard[JobT, Any, Any]:
        if issubclass(job_type, OpenBookJob):
            raise InvalidJobError("OpenBookJob is mounted via mount_book, not mount_job")
        if job_type in self._job_boards:
            raise InvalidJobError(f"{job_type.__qualname__} is already mounted")
        job_cls = getattr(board_cls, "job_cls", BaseJob)
        if job_cls is BaseJob:
            board_cls = type(
                f"{job_type.__qualname__}Board", (board_cls,), {"job_cls": job_type}
            )
        elif job_cls is not job_type:
            raise InvalidJobError(
                f"{board_cls.__name__} is for {job_cls.__qualname__}, not "
                f"{job_type.__qualname__}"
            )
        board = board_cls(self._factory)
        self._job_boards[job_type] = board
        return board

    def job_board[JobT: BaseJob](self, job_type: type[JobT]) -> BaseJobBoard[JobT, Any, Any]:
        """Return the mounted BaseJobBoard for a work BaseJob type."""
        return self._job_board(job_type)

    def attach(
        self,
        worker_id: str,
        job_type: type[BaseJob],
        slots: tuple[str, ...] | list[str],
        *,
        book: str | None = None,
    ) -> None:
        if issubclass(job_type, OpenBookJob):
            self._book_board(_book_name(job_type, book)).attach(worker_id, slots)
            return
        self._job_board(job_type).attach(worker_id, slots)

    def heartbeat(self, worker_id: str) -> None:
        for board in self._job_boards.values():
            board.heartbeat(worker_id)
        for board in self._book_boards.values():
            board.heartbeat(worker_id)

    def publish(self, job: BaseJob, *, worker_id: str, book: str | None = None) -> int:
        if isinstance(job, OpenBookJob):
            return self._book_board(_book_name(type(job), book)).publish(
                job, worker_id=worker_id
            )
        return self._job_board(type(job)).publish(job, worker_id=worker_id)

    def post_publish[JobT: BaseJob](
        self, job_type: type[JobT], *, worker_id: str, book: str | None = None
    ) -> JobT | None:
        if issubclass(job_type, OpenBookJob):
            return cast(
                JobT | None,
                self._book_board(_book_name(job_type, book)).post_publish(
                    worker_id=worker_id
                ),
            )
        return self._job_board(job_type).post_publish(worker_id=worker_id)

    def submit_post_publish(
        self, job: BaseJob, result: BaseJobResult, *, worker_id: str, book: str | None = None
    ) -> bool:
        if isinstance(job, OpenBookJob):
            return self._book_board(_book_name(type(job), book)).submit_post_publish(
                job, result, worker_id=worker_id
            )
        return self._job_board(type(job)).submit_post_publish(job, result, worker_id=worker_id)

    def claim[JobT: BaseJob](self, job_type: type[JobT], *, worker_id: str) -> JobT | None:
        if issubclass(job_type, OpenBookJob):
            raise InvalidJobError("book jobs are executed by BUS and cannot be claimed")
        return self._job_board(job_type).claim(worker_id=worker_id)

    def submit_result(self, job: BaseJob, result: BaseJobResult, *, worker_id: str) -> bool:
        if isinstance(job, OpenBookJob):
            raise InvalidJobError("book jobs complete themselves")
        return self._job_board(type(job)).submit_result(job.id, result, worker_id=worker_id)

    def post_result[JobT: BaseJob](
        self, job_type: type[JobT], *, worker_id: str
    ) -> JobT | None:
        if issubclass(job_type, OpenBookJob):
            raise InvalidJobError("book jobs complete themselves")
        return self._job_board(job_type).post_result(worker_id=worker_id)

    def submit_post_result(self, job: BaseJob, result: BaseJobResult, *, worker_id: str) -> bool:
        if isinstance(job, OpenBookJob):
            raise InvalidJobError("book jobs complete themselves")
        return self._job_board(type(job)).submit_post_result(job.id, result, worker_id=worker_id)

    @overload
    def get_result[RecordT: BaseRecord](
        self, job: OpenBookJob[RecordT], *, book: str | None = None
    ) -> OpenBookJobResult[RecordT] | None: ...

    @overload
    def get_result(self, job: BaseJob, *, book: str | None = None) -> BaseJobResult | None: ...

    def get_result(self, job: BaseJob, *, book: str | None = None) -> BaseJobResult | None:
        if isinstance(job, OpenBookJob):
            return self._book_board(_book_name(type(job), book)).get_result(job.id)
        return self._job_board(type(job)).get_result(job.id)

    def check_job_status(
        self, job: BaseJob, *, book: str | None = None
    ) -> JobStatus | None:
        if isinstance(job, OpenBookJob):
            return self._book_board(_book_name(type(job), book)).check_job_status(job.id)
        return self._job_board(type(job)).check_job_status(job.id)

    def list[JobT: BaseJob](
        self,
        job_type: type[JobT],
        *,
        status: JobStatus | None = None,
        book: str | None = None,
    ) -> list[JobT]:
        if issubclass(job_type, OpenBookJob):
            return cast(
                list[JobT],
                self._book_board(_book_name(job_type, book)).list(status=status),
            )
        return self._job_board(job_type).list(status=status)

    def close(self) -> None:
        self._factory.close()

    def __enter__(self) -> Bus:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _book_board(self, name: str) -> OpenBookJobBoard[Any]:
        try:
            return self._book_boards[name]
        except KeyError:
            raise InvalidJobError(f"book {name!r} is not provided by this BUS") from None

    def _job_board[JobT: BaseJob](self, job_type: type[JobT]) -> BaseJobBoard[JobT, Any, Any]:
        try:
            return cast(BaseJobBoard[JobT, Any, Any], self._job_boards[job_type])
        except KeyError:
            raise InvalidJobError(f"{job_type.__qualname__} is not mounted") from None


def _book_key(book_cls: type[BaseFileBook]) -> str:
    name = book_cls.name
    if not name:
        raise InvalidJobError(f"{book_cls.__name__} must set class variable name")
    return name


def _book_name(job_type: type[BaseJob], book: str | None) -> str:
    name = book or getattr(job_type, "BOOK", None)
    if not name:
        raise InvalidJobError("get/list of a OpenBookJob requires book=")
    return str(name)
