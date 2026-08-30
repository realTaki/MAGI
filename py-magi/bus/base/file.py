"""Workspace file root for Firmware file Books. Not a database."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import ClassVar

BOOK_DIRS: tuple[str, ...] = ("prompts", "skills")


def resolve_under(root: Path, name: str) -> Path:
    """Return ``root / name`` if *name* stays inside *root*."""
    if not isinstance(name, str) or not name.strip() or name.strip() != name:
        raise ValueError(f"file name must be a non-empty relative path, got {name!r}")
    relative = Path(name)
    if relative.is_absolute() or any(part == ".." for part in relative.parts):
        raise ValueError(f"file name must stay under the workspace: {name!r}")
    base = root.resolve()
    resolved = (base / relative).resolve()
    if not resolved.is_relative_to(base):
        raise ValueError(f"file name must stay under the workspace: {name!r}")
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


class FileEngine:
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

    def book(self, name: str) -> FileStore:
        """Open one FileBook-scoped store under the workspace."""
        return FileStore(self.directory(name))


class FileStore:
    """Safe filesystem primitives scoped to exactly one FileBook directory."""

    def __init__(self, directory: Path) -> None:
        self._directory = directory

    @property
    def directory(self) -> Path:
        return self._directory

    def path(self, name: str) -> Path:
        """Resolve one relative path without allowing it to leave this Book."""
        return resolve_under(self._directory, name)

    def read_text(self, name: str) -> str:
        return self.path(name).read_text(encoding="utf-8")

    def write_text(self, name: str, content: str) -> Path:
        if not isinstance(content, str):
            raise ValueError("file content must be text")
        return atomic_write(self.path(name), content)

    def exists_file(self, name: str) -> bool:
        try:
            return self.path(name).is_file()
        except ValueError:
            return False

    def exists_directory(self, name: str) -> bool:
        try:
            return self.path(name).is_dir()
        except ValueError:
            return False

    def delete_file(self, name: str) -> bool:
        path = self.path(name)
        if not path.is_file():
            return False
        path.unlink()
        return True

    def file_names(self) -> list[str]:
        if not self._directory.is_dir():
            return []
        return sorted(
            path.relative_to(self._directory).as_posix()
            for path in self._directory.rglob("*")
            if path.is_file() and not path.name.startswith(".")
        )

    def directory_names(self) -> list[str]:
        if not self._directory.is_dir():
            return []
        return sorted(path.name for path in self._directory.iterdir() if path.is_dir())

    def copy_tree(self, source: Path, name: str) -> Path:
        """Copy a directory tree into this Book at a safe relative path."""
        return shutil.copytree(source, self.path(name))
