"""BaseFileBook — a BaseBook that must live on FileBackend.

Regular Books store records through whatever Backend the Bus opened.
File Books always sit on disk as one JSON file per record, even when
the Bus primary backend is SQLite or PostgreSQL.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

from .backends.file import FileBackend, _FileStore
from .BaseBook import BaseBook
from .errors import InvalidJobError


class BaseFileBook(BaseBook):
    """File-backed Book. ``backend`` must be a :class:`FileBackend`."""

    def _require_backend(self, backend) -> None:
        if not isinstance(backend, FileBackend):
            raise InvalidJobError("BaseFileBook requires FileBackend")

    @property
    def directory(self) -> Path:
        return cast(_FileStore, self._store).directory

    def path_for(self, record_id: int) -> Path:
        return cast(_FileStore, self._store).path_for(record_id)
