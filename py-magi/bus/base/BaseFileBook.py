"""BaseFileBook — one named directory for a FileBook. Not a SQL Book.

Parallel to BaseBook, not a subclass. SQL Books use Row + Session;
file Books wrap a directory of named files under a workspace.
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from .file import FileEngine, FileStore


class BaseFileBook:
    """Directory-backed Book that composes FileStore primitives."""

    name: ClassVar[str] = "default"

    def __init__(self, engine: FileEngine) -> None:
        self._files: FileStore = engine.book(type(self).name)

    @property
    def directory(self) -> Path | None:
        return self._files.directory
