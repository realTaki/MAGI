"""Runtime BUS: Firmware JobBoards rooted at one workspace."""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, TypeVar, cast

from sqlalchemy import select

from .base.BaseJob import BaseJob, BaseJobBoard
from .base.engine import EngineFactory
from .base.file import FileEngine
from .BaseWorker import BaseWorker

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
        from .firmware.versions.schema import prepare_schema

        self._memories = self._open_sqlite("memories")
        self._logs = self._open_sqlite("logs")
        self._factory = self._memories
        prepare_schema(self._memories)
        prepare_schema(self._logs)
        self._files = FileEngine(self.workspace)
        from .firmware import create_job_boards

        self._job_boards = create_job_boards(
            self._logs,
            memories=self._memories,
            files=self._files,
        )
        self._workers: dict[str, BaseWorker] = {}
        self._closed = False

    @property
    def workers(self) -> dict[str, BaseWorker]:
        return dict(self._workers)

    def attach(
        self,
        worker: BaseWorker | Callable[[Bus], BaseWorker],
        *,
        settings: Mapping[str, str] | None = None,
    ) -> bool:
        """Store defaults, then create and attach one worker to this BUS."""
        if self._closed:
            raise ValueError("Bus is closed")
        instance = worker if isinstance(worker, BaseWorker) else worker(self)
        worker_name = instance.worker_name
        if not worker_name:
            raise ValueError(f"{type(instance).__qualname__} needs worker_name")
        if worker_name in self._workers:
            raise ValueError(f"duplicate worker_name: {worker_name}")
        if settings is not None and not self.boost_default_settings(
            worker_name=worker_name, settings=settings
        ):
            return False
        if not instance.attach():
            instance.detach()
            return False
        self._workers[worker_name] = instance
        return True

    def shutdown(self) -> None:
        """Detach workers in reverse attach order."""
        for worker in reversed(tuple(self._workers.values())):
            worker.detach()
        self._workers = {}

    def serve(self) -> None:
        """Keep this BUS and its attached workers alive until interrupted."""
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            pass
        finally:
            self.close()

    def board[JobT: BaseJob](self, job_type: type[JobT]) -> BaseJobBoard[JobT, Any, Any]:
        """Return the mounted JobBoard for *job_type*."""
        try:
            return cast(BaseJobBoard[JobT, Any, Any], self._job_boards[job_type])
        except KeyError:
            raise KeyError(f"no JobBoard mounted for {job_type.__name__}") from None

    def boost_default_settings(self, *, worker_name: str, settings: Mapping[str, str]) -> bool:
        """Register a Worker's missing Settings defaults without overwriting values."""
        from .firmware.books.settingsBook import SettingRow

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

        try:
            with self._memories.session() as session:
                for key, value in prepared.items():
                    existing = session.scalar(select(SettingRow.id).where(SettingRow.key == key))
                    if existing is None:
                        session.add(SettingRow(key=key, value=value))
                session.commit()
        except Exception:
            return False
        return True

    def close(self) -> None:
        if self._closed:
            return
        self.shutdown()
        try:
            self._logs.close()
        finally:
            self._memories.close()
            self._closed = True

    def __enter__(self) -> Bus:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

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
