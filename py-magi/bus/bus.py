"""Runtime BUS: Firmware JobBoards rooted at one workspace."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, TypeVar, cast

from sqlalchemy import select

from .base.BaseJob import BaseJob, BaseJobBoard
from .base.engine import EngineFactory
from .base.file import FileEngine

JobT = TypeVar("JobT", bound=BaseJob)


class Bus:
    """One runtime's source of truth for jobs."""

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
        from .firmware import create_job_boards

        self._job_boards = create_job_boards(
            self._factory,
            files=self._files,
        )

    def board[JobT: BaseJob](self, job_type: type[JobT]) -> BaseJobBoard[JobT, Any, Any] | None:
        """Return the mounted JobBoard for *job_type*, if Firmware shipped it."""
        return cast(BaseJobBoard[JobT, Any, Any] | None, self._job_boards.get(job_type))

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
            with self._factory.session() as session:
                for key, value in prepared.items():
                    existing = session.scalar(select(SettingRow.id).where(SettingRow.key == key))
                    if existing is None:
                        session.add(SettingRow(key=key, value=value))
                session.commit()
        except Exception:
            return False
        return True

    def close(self) -> None:
        self._factory.close()

    def __enter__(self) -> Bus:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @staticmethod
    def _setting_segment(value: str) -> str | None:
        if not isinstance(value, str) or not (normalized := value.strip()):
            return None
        if "." in normalized:
            return None
        return normalized
