"""The BUS access slice granted to one attached Worker."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, cast

from .BaseJob import BaseJob, BaseJobResult, JobStatus

if TYPE_CHECKING:
    from ..bus import Bus


class JobBoardClient[JobT: BaseJob, ResultT: BaseJobResult]:
    """One Worker's identity-bound surface for a mounted JobBoard."""

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

    def get_result(self, job_id: int) -> ResultT | None:
        return cast(ResultT | None, self._bus._job_board(self._job_type).get_result(job_id))

    def check_job_status(self, job_id: int) -> JobStatus | None:
        return self._bus._job_board(self._job_type).check_job_status(job_id)

    def list(self, *, status: JobStatus | None = None) -> list[JobT]:
        return self._bus._job_board(self._job_type).list(status=status)


class BusForWorker:
    """The attached, identity-bound BUS slice passed to one Worker.

    A slice is not a second BUS. It refers to the Runtime's one shared
    :class:`Bus`, while carrying only the identity whose Slots were allocated
    during :meth:`Bus.for_worker`.
    """

    def __init__(self, bus: Bus, worker_id: str) -> None:
        self._bus = bus
        self.worker_id = worker_id

    def board[JobT: BaseJob](self, job_type: type[JobT]) -> JobBoardClient[JobT, Any]:
        """Return this Worker's client for one mounted Job type.

        The shared BUS checks the Worker's allocated Slot for every mutating
        operation, so creating a client does not grant an undeclared capability.
        """
        return JobBoardClient(self._bus, self.worker_id, job_type)

    def boost_default_settings(self, *, worker_name: str, settings: Mapping[str, str]) -> None:
        """Register this Worker's missing defaults in the shared Settings Book."""
        self._bus.boost_default_settings(worker_name=worker_name, settings=settings)

    def heartbeat(self) -> bool:
        return self._bus.heartbeat(self.worker_id)

    def is_alive(self) -> bool:
        return self._bus.is_alive(self.worker_id)
