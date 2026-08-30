"""Composition root for one MAGI FastAPI service."""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from bus import BaseWorker, Bus
from magi.api.app import create_runtime_app
from magi.constant import WORKERS, WORKSPACE_PATH


class Magi:
    """Own one BUS, its attached workers, and its public HTTP service."""

    def __init__(
        self,
        *,
        workspace: str | Path = WORKSPACE_PATH,
        worker_types: Sequence[type[BaseWorker]] = WORKERS,
    ) -> None:
        self.bus = Bus(workspace)
        self._worker_types = tuple(worker_types)
        self._workers: dict[str, BaseWorker] = {}
        self._closed = False
        self.app = create_runtime_app(bus=self.bus)
        self.app.state.magi = self
        self.app.router.lifespan_context = self._lifespan

    @property
    def workers(self) -> dict[str, BaseWorker]:
        return dict(self._workers)

    def run(self) -> bool:
        """Attach every configured worker to this service's shared BUS."""
        if self._closed:
            raise ValueError("Magi is closed")
        if self._workers:
            raise ValueError("already running")

        prepared: list[tuple[str, BaseWorker]] = []
        for worker_type in self._worker_types:
            worker_id = worker_type.worker_name
            if not worker_id:
                raise ValueError(f"{worker_type.__qualname__} needs worker_name")
            prepared.append((worker_id, worker_type()))
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

    def shutdown(self) -> None:
        """Detach workers while retaining the BUS for controlled reuse."""
        self._detach_workers(self._workers)
        self._workers = {}

    def close(self) -> None:
        """Release the service's workers and BUS resources."""
        if self._closed:
            return
        self.shutdown()
        self.bus.close()
        self._closed = True

    def __enter__(self) -> Magi:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @asynccontextmanager
    async def _lifespan(self, _app: FastAPI):
        if not self.run():
            self.close()
            raise RuntimeError("MAGI could not attach its configured workers")
        try:
            yield
        finally:
            self.close()

    @staticmethod
    def _detach_workers(workers: dict[str, BaseWorker]) -> None:
        for worker in reversed(tuple(workers.values())):
            worker.detach()


def create_app() -> FastAPI:
    """Uvicorn factory for one default MAGI service."""
    return Magi().app
