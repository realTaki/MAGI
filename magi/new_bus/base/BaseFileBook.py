"""BaseFileBook — a BaseBook that must live on FileBackend.

Regular Books store records through whatever Backend the Bus opened.
File Books always sit on disk as one JSON file per record, even when
the Bus primary backend is SQLite or PostgreSQL.
"""

from __future__ import annotations

from pathlib import Path

from .backends.file import FileBackend
from .BaseBook import BaseBook
from .errors import InvalidJobError


class BaseFileBook(BaseBook):
    """File-backed Book. ``backend`` must be a :class:`FileBackend`."""

    def __init__(self, name: str, backend) -> None:
        if not isinstance(backend, FileBackend):
            raise InvalidJobError("BaseFileBook requires FileBackend")
        super().__init__(name, backend)
        self._files = backend

    @property
    def directory(self) -> Path:
        """Directory that holds this Book's record files."""
        return self._files.collection_dir(f"books.{self.name}")

    def path_for(self, record_id: int) -> Path:
        return self.directory / f"{record_id}.json"
