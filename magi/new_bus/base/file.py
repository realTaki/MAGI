"""File root for BaseFileBook. Not a database."""

from __future__ import annotations

from pathlib import Path


class FileBackend:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def ensure(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def close(self) -> None:
        return
