"""Composition root for one MAGI FastAPI service."""

from __future__ import annotations

import socket
from collections.abc import Sequence
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from bus import BaseWorker, Bus
from magi.api.app import create_runtime_app
from magi.constant import FIRST_PORT, LOCAL_HOST, WORKERS, workspace_path


class Magi:
    """Own one BUS, its attached workers, and its public HTTP service."""

    def __init__(
        self,
        name: str,
        *,
        worker_types: Sequence[type[BaseWorker]] = WORKERS,
    ) -> None:
        self.name = name
        self.workspace = workspace_path(name)
        self.bus = Bus(self.workspace)
        self._worker_types = tuple(worker_types)
        self._workers: dict[str, BaseWorker] = {}
        self._closed = False
        self.port: int | None = None
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

    def serve(self) -> None:
        """Run this MAGI's local API after Uvicorn starts its lifespan."""
        listener = _reserve_local_port()
        self.port = int(listener.getsockname()[1])
        server = uvicorn.Server(uvicorn.Config(self.app, host=LOCAL_HOST, port=self.port))
        try:
            server.run(sockets=[listener])
        finally:
            listener.close()

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


def _reserve_local_port() -> socket.socket:
    """Bind the first available localhost TCP port at or above FIRST_PORT."""
    for port in range(FIRST_PORT, 65536):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            listener.bind((LOCAL_HOST, port))
            listener.listen(socket.SOMAXCONN)
        except OSError:
            listener.close()
            continue
        return listener
    raise RuntimeError(f"no localhost port is available at or above {FIRST_PORT}")
