"""BaseFileBook — named files on disk. Not a SQL Book.

Parallel to BaseBook, not a subclass. SQL Books use Row + Session;
file Books wrap a directory of named files under a workspace.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import ClassVar

from .file import FileEngine, atomic_write, resolve_under


class BaseFileBook:
    """Directory-backed Book. ``engine`` must be a :class:`FileEngine`."""

    name: ClassVar[str] = "default"  # subclasses must override

    def __init__(self, engine: FileEngine) -> None:
        self._root = engine.directory(type(self).name)

    @property
    def directory(self) -> Path:
        return self._root

    def path_for(self, name: str) -> Path:
        return resolve_under(self._root, name)

    def read(self, name: str) -> str:
        return self.path_for(name).read_text(encoding="utf-8")

    def write(self, name: str, content: str) -> Path:
        if not isinstance(content, str):
            raise ValueError("file content must be text")
        return atomic_write(self.path_for(name), content)

    def exists(self, name: str) -> bool:
        try:
            return self.path_for(name).is_file()
        except ValueError:
            return False

    def delete(self, name: str) -> bool:
        path = self.path_for(name)
        if not path.is_file():
            return False
        path.unlink()
        return True

    def names(self) -> list[str]:
        if not self._root.is_dir():
            return []
        found: list[str] = []
        for path in self._root.rglob("*"):
            if not path.is_file() or path.name.startswith("."):
                continue
            found.append(path.relative_to(self._root).as_posix())
        return sorted(found)

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and self.exists(name)

    def __iter__(self) -> Iterator[str]:
        return iter(self.names())

    def __repr__(self) -> str:
        return f"{type(self).__name__}({str(self._root)!r})"
