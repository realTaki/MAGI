"""Runtime BUS: Firmware JobBoards rooted at one workspace."""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, TypeVar, cast

from .base.BaseJob import BaseJob, BaseJobBoard
from .base.engine import EngineFactory
from .base.file import FileEngine
from .BaseWorker import BaseWorker
from .firmware import create_job_boards
from .firmware.books.contactBook import Contact, ContactBook, ContactRole
from .firmware.books.settingsBook import Setting, SettingsBook
from .firmware.versions.schema import prepare_schema

JobT = TypeVar("JobT", bound=BaseJob)


class Bus:
    """One runtime's source of truth for jobs."""

    def __init__(self, handle: str, *, workspace: str | Path | None = None) -> None:
        """Open one private BUS store rooted at *workspace*.

        Books live in ``<workspace>/memories/magi.db``. Job history lives in
        ``<workspace>/logs/magi.db``. File Books share the workspace root.
        """
        self.handle = handle
        self.workspace = (
            Path(workspace).resolve()
            if workspace is not None
            else Path.home()
            / ".magi"
            / (handle[1:] if handle.startswith("@") else handle)
            / "workspace"
        )
        self.workspace.mkdir(parents=True, exist_ok=True)
        self._memories = self._open_sqlite("memories")
        self._logs = self._open_sqlite("logs")
        self._factory = self._memories
        prepare_schema(self._memories)
        prepare_schema(self._logs)
        self._files = FileEngine(self.workspace)
        self._job_boards = create_job_boards(
            self._logs,
            memories=self._memories,
            files=self._files,
        )
        ContactBook(self._memories).upsert(Contact(id=1, name=handle, role=ContactRole.MAGI))
        self._workers: dict[str, BaseWorker] = {}
        self._stopped = False

    @property
    def workers(self) -> dict[str, BaseWorker]:
        return dict(self._workers)

    def attach(
        self,
        worker: BaseWorker | Callable[[Bus], BaseWorker],
        *,
        settings: Mapping[str, str] | None = None,
    ) -> bool:
        """Create and attach one worker to this BUS.

        *settings* are startup parameters (for example CLI values from
        ``magi.py``). They are boosted onto the BUS before the worker
        starts, replacing that worker's defaults for those keys.
        """
        if self._stopped:
            raise ValueError("Bus is stopped")
        instance = worker if isinstance(worker, BaseWorker) else worker(self)
        worker_name = instance.worker_name
        if not worker_name:
            raise ValueError(f"{type(instance).__qualname__} needs worker_name")
        if worker_name in self._workers:
            raise ValueError(f"duplicate worker_name: {worker_name}")
        if settings is not None:
            self.boost_settings(worker_name=worker_name, settings=settings)
        if not instance.attach():
            instance.detach()
            return False
        self._workers[worker_name] = instance
        return True

    def start(self) -> None:
        """Keep this BUS running until interrupted."""
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            pass

    def stop(self) -> None:
        """Detach workers and close the BUS stores."""
        if self._stopped:
            return
        for worker in reversed(tuple(self._workers.values())):
            worker.detach()
        self._workers = {}
        try:
            self._logs.close()
        finally:
            self._memories.close()
            self._stopped = True

    def board[JobT: BaseJob](self, job_type: type[JobT]) -> BaseJobBoard[JobT, Any, Any] | None:
        """Return the mounted JobBoard for *job_type*, or None if it is not mounted."""
        mounted = self._job_boards.get(job_type)
        return None if mounted is None else cast(BaseJobBoard[JobT, Any, Any], mounted)

    def boost_default_settings(self, *, worker_name: str, settings: Mapping[str, str]) -> bool:
        """Insert a Worker's missing Settings defaults without overwriting values."""
        return self._write_settings(worker_name, settings, overwrite=False)

    def boost_settings(self, *, worker_name: str, settings: Mapping[str, str]) -> bool:
        """Persist startup parameters for a Worker, replacing existing values."""
        return self._write_settings(worker_name, settings, overwrite=True)

    def _write_settings(
        self,
        worker_name: str,
        settings: Mapping[str, str],
        *,
        overwrite: bool,
    ) -> bool:
        namespace = self._setting_segment(worker_name)
        if namespace is None:
            return False
        prepared = {
            f"{namespace}.{segment}": value
            for name, value in settings.items()
            if (segment := self._setting_segment(name)) is not None
        }
        if len(prepared) != len(settings) or not all(
            isinstance(value, str) for value in prepared.values()
        ):
            return False
        book = SettingsBook(self._memories)
        for key, value in prepared.items():
            if not overwrite and book.get(key) is not None:
                continue
            book.upsert(Setting(key=key, value=value))
        return True

    def __enter__(self) -> Bus:
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()

    def _open_sqlite(self, folder: str) -> EngineFactory:
        database_path = self.workspace / folder / "magi.db"
        database_path.parent.mkdir(parents=True, exist_ok=True)
        return EngineFactory(f"sqlite:///{database_path}")

    @staticmethod
    def _setting_segment(value: str) -> str | None:
        if not isinstance(value, str) or not (normalized := value.strip()):
            return None
        if "." in normalized:
            return None
        return normalized
