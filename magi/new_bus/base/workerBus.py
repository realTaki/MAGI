"""Typed worker-facing BUS views and JobBoard descriptors."""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, Any, ClassVar, cast

from .BaseJob import BaseJob, BaseJobBoard, BaseJobResult
from .heartbeat import Slot

if TYPE_CHECKING:
    from ..bus import Bus


class JobBoardClient[JobT: BaseJob, ResultT: BaseJobResult]:
    """A typed Board surface bound to one worker identity."""

    def __init__(self, bus: Bus, worker_id: str, job_type: type[JobT]) -> None:
        self._bus = bus
        self._worker_id = worker_id
        self._job_type = job_type

    def publish(self, job: JobT) -> int:
        return int(self._bus._invoke(self._worker_id, self._job_type, "publish", job) or 0)

    def post_publish(self) -> JobT | None:
        return cast(JobT | None, self._bus._invoke(self._worker_id, self._job_type, "post_publish"))

    def submit_post_publish(self, job: JobT, result: BaseJobResult) -> bool:
        return bool(
            self._bus._invoke(self._worker_id, self._job_type, "submit_post_publish", job, result)
        )

    def claim(self) -> JobT | None:
        return cast(JobT | None, self._bus._invoke(self._worker_id, self._job_type, "claim"))

    def submit_result(self, result: ResultT) -> bool:
        return bool(self._bus._invoke(self._worker_id, self._job_type, "submit_result", result))

    def post_result(self) -> JobT | None:
        return cast(JobT | None, self._bus._invoke(self._worker_id, self._job_type, "post_result"))

    def submit_post_result(self, job_id: int, result: ResultT) -> bool:
        return bool(
            self._bus._invoke(self._worker_id, self._job_type, "submit_post_result", job_id, result)
        )


class JobBoardBinding[JobT: BaseJob, ResultT: BaseJobResult]:
    """Declare a typed Board property and the slots a worker needs from it."""

    def __init__(
        self,
        board_cls: type[BaseJobBoard[JobT, ResultT, Any]],
        slots: Iterable[str],
    ) -> None:
        self.board_cls = board_cls
        self.slots = tuple(slots)
        self.name = ""

    def __set_name__(self, _owner: type[WorkerBus], name: str) -> None:
        self.name = name

    def __get__(self, instance: WorkerBus | None, _owner: type[WorkerBus]) -> Any:
        if instance is None:
            return self
        return JobBoardClient(instance._bus, instance.worker_id, self.board_cls.job_cls)


def job_board[JobT: BaseJob, ResultT: BaseJobResult](
    board_cls: type[BaseJobBoard[JobT, ResultT, Any]], *, slots: Iterable[str]
) -> JobBoardBinding[JobT, ResultT]:
    return JobBoardBinding(board_cls, slots)


class WorkerBus:
    """Per-worker typed view backed by the runtime's one Bus instance."""

    _bindings: ClassVar[tuple[JobBoardBinding[Any, Any], ...]] = ()

    def __init_subclass__(cls) -> None:
        super().__init_subclass__()
        inherited = tuple(
            binding for parent in cls.__mro__[1:] for binding in getattr(parent, "_bindings", ())
        )
        own = tuple(value for value in vars(cls).values() if isinstance(value, JobBoardBinding))
        cls._bindings = inherited + own

    def __init__(self, bus: Bus, worker_id: str) -> None:
        self._bus = bus
        self.worker_id = worker_id

    def attach(self) -> bool:
        return self._bus.attach(self.worker_id, type(self).declared_slots())

    @classmethod
    def declared_slots(cls) -> tuple[Slot, ...]:
        return tuple(
            Slot(binding.board_cls.job_cls, slot)
            for binding in cls._bindings
            for slot in binding.slots
        )

    def heartbeat(self) -> bool:
        return self._bus.heartbeat(self.worker_id)

    def is_alive(self) -> bool:
        return self._bus.is_alive(self.worker_id)
