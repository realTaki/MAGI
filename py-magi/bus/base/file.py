"""Workspace file root for Firmware file Books. Not a database."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import ClassVar

BOOK_DIRS: tuple[str, ...] = ("prompts", "skills")


def _resolve_under(root: Path, name: str) -> Path | None:
    """Return a safe path under *root*, or None for an invalid path."""
    if not isinstance(name, str) or not name.strip() or name.strip() != name:
        return None
    relative = Path(name)
    if relative.is_absolute() or any(part == ".." for part in relative.parts):
        return None
    try:
        base = root.resolve()
        resolved = (base / relative).resolve()
    except OSError:
        return None
    return resolved if resolved.is_relative_to(base) else None


class FileEngine:
    """One MAGI workspace tree. File Books live in named folders under it."""

    book_dirs: ClassVar[tuple[str, ...]] = BOOK_DIRS

    def __init__(self, workspace: str | Path) -> None:
        self.root = Path(workspace).resolve()
        self.available = self._ensure_directory(self.root)
        for name in self.book_dirs:
            self.available = self._ensure_directory(self.root / name) and self.available

    @staticmethod
    def _ensure_directory(path: Path) -> bool:
        try:
            path.mkdir(parents=True, exist_ok=True)
            return path.is_dir()
        except OSError:
            return False

    def directory(self, name: str) -> Path | None:
        """Return a Book folder, or None when it cannot be opened."""
        path = _resolve_under(self.root, name)
        if path is None or not self._ensure_directory(path):
            return None
        return path

    def book(self, name: str) -> FileStore:
        """Open one FileBook-scoped store under the workspace."""
        return FileStore(self.directory(name))


class FileStore:
    """Safe filesystem primitives scoped to exactly one FileBook directory."""

    def __init__(self, directory: Path | None) -> None:
        self._directory = directory

    @property
    def directory(self) -> Path | None:
        return self._directory

    def path(self, name: str) -> Path | None:
        """Resolve one relative path, or None if it is invalid or unavailable."""
        if self._directory is None:
            return None
        return _resolve_under(self._directory, name)

    def read_text(self, name: str) -> str | None:
        path = self.path(name)
        if path is None:
            return None
        try:
            return path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return None

    def write_text(self, name: str, content: str) -> bool:
        if not isinstance(content, str):
            return False
        path = self.path(name)
        if path is None:
            return False
        fd: int | None = None
        tmp_path: str | None = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_path = tempfile.mkstemp(
                prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
            )
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                fd = None
                handle.write(content)
            os.replace(tmp_path, path)
        except Exception:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
            try:
                if tmp_path is not None:
                    os.unlink(tmp_path)
            except OSError:
                pass
            return False
        return True

    def exists_file(self, name: str) -> bool:
        path = self.path(name)
        if path is None:
            return False
        try:
            return path.is_file()
        except OSError:
            return False

    def exists_directory(self, name: str) -> bool:
        path = self.path(name)
        if path is None:
            return False
        try:
            return path.is_dir()
        except OSError:
            return False

    def delete_file(self, name: str) -> bool:
        path = self.path(name)
        if path is None:
            return False
        try:
            if not path.is_file():
                return False
            path.unlink()
        except OSError:
            return False
        return True

    def file_names(self) -> list[str]:
        if self._directory is None:
            return []
        try:
            return sorted(
                path.relative_to(self._directory).as_posix()
                for path in self._directory.rglob("*")
                if path.is_file() and not path.name.startswith(".")
            )
        except OSError:
            return []

    def directory_names(self) -> list[str]:
        if self._directory is None:
            return []
        try:
            return sorted(path.name for path in self._directory.iterdir() if path.is_dir())
        except OSError:
            return []

    def copy_tree(self, source: Path, name: str) -> bool:
        """Copy a directory tree into this Book, returning False on failure."""
        target = self.path(name)
        if target is None:
            return False
        try:
            shutil.copytree(source, target)
        except Exception:
            return False
        return True
