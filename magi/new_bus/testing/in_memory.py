"""In-memory EngineFactory for tests. Not part of the official backend set."""

from __future__ import annotations

from ..base.engine import SQLiteBackend


class InMemoryBackend(SQLiteBackend):
    def __init__(self) -> None:
        super().__init__(memory=True)
