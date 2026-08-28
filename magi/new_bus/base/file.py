"""Workspace file root for Firmware file Books. Not a database."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import ClassVar

from .errors import InvalidJobError

BOOK_DIRS: tuple[str, ...] = ("prompts", "skills")


def resolve_under(root: Path, name: str) -> Path:
    """Return ``root / name`` if *name* stays inside *root*."""
    if not isinstance(name, str) or not name.strip() or name.strip() != name:
        raise InvalidJobError(f"file name must be a non-empty relative path, got {name!r}")
    relative = Path(name)
    if relative.is_absolute() or any(part == ".." for part in relative.parts):
        raise InvalidJobError(f"file name must stay under the workspace: {name!r}")
    base = root.resolve()
    resolved = (base / relative).resolve()
    if not resolved.is_relative_to(base):
        raise InvalidJobError(f"file name must stay under the workspace: {name!r}")
    return resolved


def atomic_write(path: Path, content: str) -> Path:
    """Write *content* so readers never see a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    return path


class FileBackend:
    """One MAGI workspace tree. File Books live in named folders under it."""

    book_dirs: ClassVar[tuple[str, ...]] = BOOK_DIRS

    def __init__(self, workspace: str | Path) -> None:
        self.root = Path(workspace).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        for name in self.book_dirs:
            (self.root / name).mkdir(parents=True, exist_ok=True)

    def directory(self, name: str) -> Path:
        """Return one book folder, creating it if needed."""
        path = resolve_under(self.root, name)
        path.mkdir(parents=True, exist_ok=True)
        return path
