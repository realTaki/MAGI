"""In-memory Backend for tests. Not part of the official backend set."""

from __future__ import annotations

from ..base.backends.sqlite import SQLiteBackend


class InMemoryBackend(SQLiteBackend):
    def __init__(self) -> None:
        super().__init__(memory=True)
