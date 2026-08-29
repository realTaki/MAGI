"""Runtime BUS: JobBoards, shared heartbeat, and Slot routing."""

from __future__ import annotations

import threading
from collections.abc import Iterable
from pathlib import Path
from typing import Any, TypeVar, cast

from .base.BaseJob import BaseJob, BaseJobBoard
from .base.engine import EngineFactory
from .base.file import FileEngine
from .base.heartbeat import Heartbeat, Slot
from .bus_for_worker import BusForWorker

JobT = TypeVar("JobT", bound=BaseJob)


class Bus:
    """One runtime's source of truth for jobs, Slots, and liveness."""

    def __init__(self, workspace: str | Path) -> None:
        """Open one private BUS store rooted at *workspace*.

        The current Firmware has no MAGIS storage yet, so its SQL state always
        lives in ``<workspace>/memories/magi.db``.  File Books share the same
        workspace root.  Backend construction remains BUS-private until a
        MAGIS-aware storage contract is introduced.
        """
        self.workspace = Path(workspace).resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        database_path = self.workspace / "memories" / "magi.db"
        database_path.parent.mkdir(parents=True, exist_ok=True)
        self._factory = EngineFactory(f"sqlite:///{database_path}")
        self._files = FileEngine(self.workspace)
        from .firmware.versions.schema import prepare_schema

        prepare_schema(self._factory)
        self._heartbeat = Heartbeat()
        from .firmware import create_job_boards

        self._job_boards = create_job_boards(
            self._factory,
            self._heartbeat,
            files=self._files,
        )
        self._lock = threading.RLock()

    def for_worker(
        self,
        worker_id: str,
        slots: Iterable[Slot],
    ) -> BusForWorker | None:
        """Allocate Slots and return the shared BUS slice for one Worker."""
        if not self._allocate_worker_slots(worker_id, slots):
            return None
        return BusForWorker(
            bus=self,
            factory=self._factory,
            heartbeat=self._heartbeat,
            worker_id=worker_id,
        )

    def _allocate_worker_slots(self, worker_id: str, slots: Iterable[Slot]) -> bool:
        """Attach one Worker to each of its declared Slots."""
        requested = tuple(slots)
        with self._lock:
            if any(
                (board := self._job_board(slot.job_type)) is None or not board.has_slot(slot.name)
                for slot in requested
            ):
                return False
            if not self._heartbeat.attach(worker_id, requested):
                return False
        return True

    def _invoke(self, worker_id: str, job_type: type[JobT], slot_name: str, *args, **kwargs) -> Any:
        board = self._job_board(job_type)
        if board is None:
            return None
        slot = Slot(job_type, slot_name)
        try:
            if not self._heartbeat.holds(worker_id, slot):
                return None
            return getattr(board, slot_name)(*args, worker_id=worker_id, **kwargs)
        except Exception:
            # A worker-facing BUS call must not leak backend failures. Jobs
            # that already exist turn failures into durable FAILED results at
            # their execution boundary; calls with no persisted Job use their
            # ordinary empty result instead.
            return None

    def close(self) -> None:
        self._factory.close()

    def __enter__(self) -> Bus:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _job_board(self, job_type: type[JobT]) -> BaseJobBoard[JobT, Any, Any] | None:
        return cast(BaseJobBoard[JobT, Any, Any] | None, self._job_boards.get(job_type))
