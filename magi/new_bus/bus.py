"""Public BUS surface. External components never receive a BaseBook."""

from __future__ import annotations

from typing import Any, cast

from .base.BaseFileBook import BaseFileBook
from .base.BaseJob import BaseJob, BaseJobBoard, BaseJobResult, JobStatus
from .base.engine import EngineFactory
from .base.errors import InvalidJobError
from .base.file import FileBackend


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
        self._books: dict[str, BaseFileBook] = {}
        self._job_boards: dict[type[BaseJob], BaseJobBoard[Any, Any, Any]] = {}
        from .firmware import attach

        attach(self)

    @property
    def books(self) -> tuple[str, ...]:
        return tuple(self._books)

    @property
    def jobs(self) -> tuple[type[BaseJob], ...]:
        return tuple(self._job_boards)

    def mount_book(self, mounted: type[BaseFileBook]) -> None:
        name = _book_key(mounted)
        if name in self._books:
            raise InvalidJobError(f"book {name!r} is already mounted")
        if self._files is None:
            raise InvalidJobError("BaseFileBook requires a FileBackend")
        self._books[name] = mounted(self._files)

    def mount_job[JobT: BaseJob](
        self,
        job_type: type[JobT],
        *,
        board_cls: type[BaseJobBoard[Any, Any, Any]] = BaseJobBoard,
    ) -> BaseJobBoard[JobT, Any, Any]:
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
    ) -> None:
        self._job_board(job_type).attach(worker_id, slots)

    def heartbeat(self, worker_id: str) -> None:
        for board in self._job_boards.values():
            board.heartbeat(worker_id)

    def publish(self, job: BaseJob, *, worker_id: str) -> int:
        return self._job_board(type(job)).publish(job, worker_id=worker_id)

    def post_publish[JobT: BaseJob](
        self, job_type: type[JobT], *, worker_id: str
    ) -> JobT | None:
        return self._job_board(job_type).post_publish(worker_id=worker_id)

    def submit_post_publish(
        self, job: BaseJob, result: BaseJobResult, *, worker_id: str
    ) -> bool:
        return self._job_board(type(job)).submit_post_publish(job, result, worker_id=worker_id)

    def claim[JobT: BaseJob](self, job_type: type[JobT], *, worker_id: str) -> JobT | None:
        return self._job_board(job_type).claim(worker_id=worker_id)

    def submit_result(self, job: BaseJob, result: BaseJobResult, *, worker_id: str) -> bool:
        return self._job_board(type(job)).submit_result(result, worker_id=worker_id)

    def post_result[JobT: BaseJob](
        self, job_type: type[JobT], *, worker_id: str
    ) -> JobT | None:
        return self._job_board(job_type).post_result(worker_id=worker_id)

    def submit_post_result(self, job: BaseJob, result: BaseJobResult, *, worker_id: str) -> bool:
        return self._job_board(type(job)).submit_post_result(job.id, result, worker_id=worker_id)

    def get_result(self, job: BaseJob) -> Any:
        return self._job_board(type(job)).get_result(job.id)

    def check_job_status(self, job: BaseJob) -> JobStatus | None:
        return self._job_board(type(job)).check_job_status(job.id)

    def list[JobT: BaseJob](
        self,
        job_type: type[JobT],
        *,
        status: JobStatus | None = None,
    ) -> list[JobT]:
        return self._job_board(job_type).list(status=status)

    def close(self) -> None:
        self._factory.close()

    def __enter__(self) -> Bus:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

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
