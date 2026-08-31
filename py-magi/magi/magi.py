"""One MAGI runtime: its shared BUS and attached workers."""

from __future__ import annotations

import threading
from collections.abc import Callable, Sequence

from bus import BaseWorker, Bus

from .constant import WORKERS, workspace_path

WorkerFactory = Callable[[], BaseWorker]


class Magi:
    """Own one BUS and the workers attached to it.

    Channel implementations belong to ``channels.*``. The composition root
    supplies them as ordinary worker factories alongside domain workers.
    """

    def __init__(
        self,
        handle: str,
        *,
        worker_types: Sequence[WorkerFactory] = WORKERS,
    ) -> None:
        self.handle = handle
        self.workspace = workspace_path(handle)
        self.bus = Bus(self.workspace)
        self._worker_types = tuple(worker_types)
        self._workers: dict[str, BaseWorker] = {}
        self._closed = False

    @property
    def workers(self) -> dict[str, BaseWorker]:
        return dict(self._workers)

    def run(self) -> bool:
        """Attach every configured worker to this MAGI's shared BUS."""
        if self._closed:
            raise ValueError("Magi is closed")
        if self._workers:
            raise ValueError("already running")

        prepared: list[tuple[str, BaseWorker]] = []
        for worker_type in self._worker_types:
            worker = worker_type()
            worker_id = worker.worker_name
            if not worker_id:
                raise ValueError(f"{type(worker).__qualname__} needs worker_name")
            prepared.append((worker_id, worker))
        if not prepared:
            raise ValueError("no workers")
        if len({worker_id for worker_id, _ in prepared}) != len(prepared):
            raise ValueError("duplicate worker_id")

        attached: dict[str, BaseWorker] = {}
        for worker_id, worker in prepared:
            if not worker.attach(self.bus):
                worker.detach()
                self._detach_workers(attached)
                return False
            attached[worker_id] = worker
        self._workers = attached
        return True

    def serve(self) -> None:
        """Attach workers and keep the process alive until interrupted."""
        if not self.run():
            self.close()
            raise RuntimeError("MAGI could not attach its configured workers")
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            pass
        finally:
            self.close()

    def shutdown(self) -> None:
        """Detach workers while retaining the BUS for controlled reuse."""
        self._detach_workers(self._workers)
        self._workers = {}

    def close(self) -> None:
        if self._closed:
            return
        self.shutdown()
        self.bus.close()
        self._closed = True

    def __enter__(self) -> Magi:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @staticmethod
    def _detach_workers(workers: dict[str, BaseWorker]) -> None:
        for worker in reversed(tuple(workers.values())):
            worker.detach()
