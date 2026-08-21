"""Runtime BUS: JobBoards, shared heartbeat, and slot routing."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, TypeVar, cast

from .base.BaseFileBook import BaseFileBook
from .base.BaseJob import BaseJob, BaseJobBoard, BaseJobResult, JobStatus
from .base.dock import OrDock
from .base.engine import EngineFactory
from .base.errors import InvalidJobError
from .base.file import FileBackend
from .base.heartbeat import Heartbeat, Slot
from .base.workerBus import WorkerBus

JobT = TypeVar("JobT", bound=BaseJob)
WorkerBusT = TypeVar("WorkerBusT", bound=WorkerBus)


class Bus:
    """One runtime's source of truth for jobs, slots, docks, and liveness."""

    def __init__(self, factory: EngineFactory, *, files: FileBackend | None = None) -> None:
        if not isinstance(factory, EngineFactory):
            raise InvalidJobError("Bus requires EngineFactory")
        self._factory = factory
        self._files = files
        if files is not None:
            files.ensure()
        from .firmware.versions.schema import prepare_schema

        prepare_schema(factory)
        self._heartbeat = Heartbeat()
        self._books: dict[str, BaseFileBook] = {}
        self._job_boards: dict[type[BaseJob], BaseJobBoard[Any, Any, Any]] = {}
        self._docks: dict[Slot, OrDock] = {}
        self._worker_docks: dict[str, set[OrDock]] = {}
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

    def mount_job(
        self,
        job_type: type[JobT],
        *,
        board_cls: type[BaseJobBoard[Any, Any, Any]] = BaseJobBoard,
    ) -> BaseJobBoard[JobT, Any, Any]:
        if job_type in self._job_boards:
            raise InvalidJobError(f"{job_type.__qualname__} is already mounted")
        declared = getattr(board_cls, "job_cls", BaseJob)
        if declared is BaseJob:
            board_cls = type(f"{job_type.__qualname__}Board", (board_cls,), {"job_cls": job_type})
        elif declared is not job_type:
            raise InvalidJobError(
                f"{board_cls.__name__} is for {declared.__qualname__}, not {job_type.__qualname__}"
            )
        board = board_cls(self._factory, self._heartbeat)
        self._job_boards[job_type] = board
        return cast(BaseJobBoard[JobT, Any, Any], board)

    def job_board(self, job_type: type[JobT]) -> BaseJobBoard[JobT, Any, Any]:
        return self._job_board(job_type)

    def for_worker(
        self,
        worker_id: str,
        view_cls: type[WorkerBusT] = WorkerBus,
    ) -> WorkerBusT:
        return view_cls(self, worker_id)

    def install_or_dock(self, slot: Slot) -> bool:
        if slot.job_type not in self._job_boards or not self._job_board(slot.job_type).has_slot(
            slot.name
        ):
            return False
        if slot in self._docks:
            return True
        self._docks[slot] = OrDock(self._heartbeat, slot)
        return True

    def attach(self, worker_id: str, slots: Iterable[Slot]) -> bool:
        """Attach a worker's declared slots, routing each through its Dock if needed."""
        requested = tuple(slots)
        if any(
            slot.job_type not in self._job_boards
            or not self._job_board(slot.job_type).has_slot(slot.name)
            for slot in requested
        ):
            return False
        direct = tuple(slot for slot in requested if slot not in self._docks)
        if not self._heartbeat.attach(worker_id, direct):
            return False
        for slot in requested:
            dock = self._docks.get(slot)
            if dock is None:
                continue
            if not dock.attach(worker_id):
                return False
            self._worker_docks.setdefault(worker_id, set()).add(dock)
        return True

    def heartbeat(self, worker_id: str) -> bool:
        if not self._heartbeat.heartbeat(worker_id):
            return False
        return all(dock.heartbeat(worker_id) for dock in self._worker_docks.get(worker_id, ()))

    def is_alive(self, worker_id: str) -> bool:
        return self._heartbeat.is_alive(worker_id)

    def _invoke(self, worker_id: str, job_type: type[JobT], slot_name: str, *args, **kwargs) -> Any:
        slot = Slot(job_type, slot_name)
        board = self._job_board(job_type)
        dock = self._docks.get(slot)
        if dock is not None:
            return dock.call(worker_id, board, *args, **kwargs)
        if not self._heartbeat.holds(worker_id, slot):
            return None
        return getattr(board, slot_name)(*args, worker_id=worker_id, **kwargs)

    def publish(self, job: BaseJob, *, worker_id: str) -> int:
        return int(self._invoke(worker_id, type(job), "publish", job) or 0)

    def post_publish(self, job_type: type[JobT], *, worker_id: str) -> JobT | None:
        return cast(JobT | None, self._invoke(worker_id, job_type, "post_publish"))

    def submit_post_publish(self, job: BaseJob, result: BaseJobResult, *, worker_id: str) -> bool:
        return bool(self._invoke(worker_id, type(job), "submit_post_publish", job, result))

    def claim(self, job_type: type[JobT], *, worker_id: str) -> JobT | None:
        return cast(JobT | None, self._invoke(worker_id, job_type, "claim"))

    def submit_result(self, job: BaseJob, result: BaseJobResult, *, worker_id: str) -> bool:
        return bool(self._invoke(worker_id, type(job), "submit_result", result))

    def post_result(self, job_type: type[JobT], *, worker_id: str) -> JobT | None:
        return cast(JobT | None, self._invoke(worker_id, job_type, "post_result"))

    def submit_post_result(self, job: BaseJob, result: BaseJobResult, *, worker_id: str) -> bool:
        return bool(self._invoke(worker_id, type(job), "submit_post_result", job.id, result))

    def get_result(self, job: BaseJob) -> Any:
        return self._job_board(type(job)).get_result(job.id)

    def check_job_status(self, job: BaseJob) -> JobStatus | None:
        return self._job_board(type(job)).check_job_status(job.id)

    def list(self, job_type: type[JobT], *, status: JobStatus | None = None) -> list[JobT]:
        return self._job_board(job_type).list(status=status)

    def close(self) -> None:
        self._factory.close()

    def __enter__(self) -> Bus:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _job_board(self, job_type: type[JobT]) -> BaseJobBoard[JobT, Any, Any]:
        try:
            return cast(BaseJobBoard[JobT, Any, Any], self._job_boards[job_type])
        except KeyError:
            raise InvalidJobError(f"{job_type.__qualname__} is not mounted") from None


def _book_key(book_cls: type[BaseFileBook]) -> str:
    if not book_cls.name:
        raise InvalidJobError(f"{book_cls.__name__} must set class variable name")
    return book_cls.name
