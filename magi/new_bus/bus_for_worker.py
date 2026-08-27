"""The BUS access slice granted to one attached Worker."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import select

from .base.BaseJob import BaseJob, BaseJobResult, JobStatus
from .base.dock import AndDock, OrDock
from .base.engine import EngineFactory
from .base.heartbeat import Heartbeat

if TYPE_CHECKING:
    from .bus import Bus


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

    A slice is not a second BUS. It refers to the Runtime's one shared BUS,
    while holding the dependencies needed for its Worker-facing operations.
    """

    def __init__(
        self,
        *,
        bus: Bus,
        factory: EngineFactory,
        heartbeat: Heartbeat,
        worker_docks: dict[str, set[OrDock | AndDock]],
        worker_id: str,
    ) -> None:
        self._bus = bus
        self._factory = factory
        self._heartbeat = heartbeat
        self._worker_docks = worker_docks
        self.worker_id = worker_id

    def board[JobT: BaseJob](self, job_type: type[JobT]) -> JobBoardClient[JobT, Any]:
        """Return this Worker's client for one mounted Job type."""
        return JobBoardClient(self._bus, self.worker_id, job_type)

    def boost_default_settings(self, *, worker_name: str, settings: Mapping[str, str]) -> None:
        """Register this Worker's missing Settings defaults without overwriting values."""
        from .firmware.books.settingsBook import SettingRow

        namespace = self._setting_segment(worker_name, label="worker name")
        prepared = {
            f"{namespace}.{self._setting_segment(name, label='setting name')}": value
            for name, value in settings.items()
        }
        if not all(isinstance(value, str) for value in prepared.values()):
            raise ValueError("default setting values must be strings")

        with self._factory.session() as session:
            for key, value in prepared.items():
                existing = session.scalar(select(SettingRow.id).where(SettingRow.key == key))
                if existing is None:
                    session.add(SettingRow(key=key, value=value))
            session.commit()

    def heartbeat(self) -> bool:
        if not self._heartbeat.heartbeat(self.worker_id):
            return False
        return all(dock.heartbeat(self.worker_id) for dock in self._worker_docks.get(self.worker_id, ()))

    def is_alive(self) -> bool:
        return self._heartbeat.is_alive(self.worker_id)

    @staticmethod
    def _setting_segment(value: str, *, label: str) -> str:
        if not isinstance(value, str) or not (normalized := value.strip()):
            raise ValueError(f"{label} must be non-empty")
        if "." in normalized:
            raise ValueError(f"{label} must not contain '.'")
        return normalized
