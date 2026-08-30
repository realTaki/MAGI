"""The BUS access slice granted to one attached Worker."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import select

from .base.BaseJob import BaseJob, BaseJobResult, JobStatus
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

    def claim_post_publish(self) -> JobT | None:
        return cast(
            JobT | None, self._bus._invoke(self._worker_id, self._job_type, "claim_post_publish")
        )

    def submit_post_publish(self, result: BaseJobResult) -> bool:
        return bool(
            self._bus._invoke(self._worker_id, self._job_type, "submit_post_publish", result)
        )

    def claim(self) -> JobT | None:
        return cast(JobT | None, self._bus._invoke(self._worker_id, self._job_type, "claim"))

    def submit_result(self, result: ResultT) -> bool:
        return bool(self._bus._invoke(self._worker_id, self._job_type, "submit_result", result))

    def claim_post_result(self) -> JobT | None:
        return cast(
            JobT | None, self._bus._invoke(self._worker_id, self._job_type, "claim_post_result")
        )

    def get_result(self, job_id: int) -> ResultT | None:
        board = self._bus._job_board(self._job_type)
        if board is None:
            return None
        try:
            return cast(ResultT | None, board.get_result(job_id))
        except Exception:
            return None

    def check_job_status(self, job_id: int) -> JobStatus | None:
        board = self._bus._job_board(self._job_type)
        if board is None:
            return None
        try:
            return board.check_job_status(job_id)
        except Exception:
            return None

    def list(self, *, status: JobStatus | None = None) -> list[JobT]:
        board = self._bus._job_board(self._job_type)
        if board is None:
            return []
        try:
            return board.list(status=status)
        except Exception:
            return []


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
        worker_id: str,
    ) -> None:
        self._bus = bus
        self._factory = factory
        self._heartbeat = heartbeat
        self.worker_id = worker_id

    def board[JobT: BaseJob](self, job_type: type[JobT]) -> JobBoardClient[JobT, Any]:
        """Return this Worker's client for one mounted Job type."""
        return JobBoardClient(self._bus, self.worker_id, job_type)

    def boost_default_settings(self, *, worker_name: str, settings: Mapping[str, str]) -> bool:
        """Register this Worker's missing Settings defaults without overwriting values."""
        from .firmware.books.settingsBook import SettingRow

        namespace = self._setting_segment(worker_name)
        if namespace is None:
            return False
        prepared = {
            f"{namespace}.{segment}": value
            for name, value in settings.items()
            if (segment := self._setting_segment(name)) is not None
        }
        if len(prepared) != len(settings) or not all(isinstance(value, str) for value in prepared.values()):
            return False

        try:
            with self._factory.session() as session:
                for key, value in prepared.items():
                    existing = session.scalar(select(SettingRow.id).where(SettingRow.key == key))
                    if existing is None:
                        session.add(SettingRow(key=key, value=value))
                session.commit()
        except Exception:
            return False
        return True

    def heartbeat(self) -> bool:
        return self._heartbeat.heartbeat(self.worker_id)

    def is_alive(self) -> bool:
        return self._heartbeat.is_alive(self.worker_id)

    @staticmethod
    def _setting_segment(value: str) -> str | None:
        if not isinstance(value, str) or not (normalized := value.strip()):
            return None
        if "." in normalized:
            return None
        return normalized
