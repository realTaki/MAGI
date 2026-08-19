"""BaseFileBook — named files on disk. Not a SQL Book.

Parallel to BaseBook, not a subclass. SQL Books use Row + Session;
file Books wrap a directory of named files.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from typing import ClassVar

from .file import FileBackend
from .errors import InvalidJobError


class BaseFileBook:
    """Directory-backed Book. ``backend`` must be a :class:`FileBackend`."""

    name: ClassVar[str] = ""

    def __init__(self, backend) -> None:
        if not type(self).name:
            raise InvalidJobError(f"{type(self).__name__} must set class variable name")
        if not isinstance(backend, FileBackend):
            raise InvalidJobError("BaseFileBook requires FileBackend")
        self._root = backend.root / type(self).name
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def directory(self) -> Path:
        return self._root

    def path_for(self, name: str) -> Path:
        return self._root / name

    def read(self, name: str) -> str:
        return self.path_for(name).read_text(encoding="utf-8")

    def write(self, name: str, content: str) -> Path:
        path = self.path_for(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, path)
        return path

    def exists(self, name: str) -> bool:
        return self.path_for(name).is_file()

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and self.exists(name)

    def __iter__(self) -> Iterator[str]:
        if not self._root.is_dir():
            return iter(())
        return iter(sorted(path.name for path in self._root.iterdir() if path.is_file()))

    def __repr__(self) -> str:
        return f"{type(self).__name__}({str(self._root)!r})"
