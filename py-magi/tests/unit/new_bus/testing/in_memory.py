"""In-memory backend fixture for BUS tests."""

from magi.bus.base.engine import SQLiteBackend


class InMemoryBackend(SQLiteBackend):
    def __init__(self) -> None:
        super().__init__(memory=True)
