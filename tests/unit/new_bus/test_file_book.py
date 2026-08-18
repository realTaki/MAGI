from __future__ import annotations

import pytest

from magi.new_bus import (
    BookOp,
    Bus,
    FileBackend,
    InvalidJobError,
    JobStatus,
    ManageBookJob,
    SQLiteBackend,
)
from magi.new_bus.base.BaseFileBook import BaseFileBook
from magi.new_bus.testing import InMemoryBackend


class NotesBook(BaseFileBook):
    name = "notes"


def test_file_book_requires_file_backend(tmp_path) -> None:
    with Bus(InMemoryBackend()) as bus:
        with pytest.raises(InvalidJobError, match="FileBackend"):
            bus.mount_book(NotesBook)

    with Bus(SQLiteBackend(tmp_path / "bus.sqlite")) as bus:
        with pytest.raises(InvalidJobError, match="FileBackend"):
            bus.mount_book(NotesBook)


def test_file_book_uses_disk_when_bus_is_sqlite(tmp_path) -> None:
    sqlite = SQLiteBackend(tmp_path / "bus.sqlite")
    files = FileBackend(tmp_path / "files")
    with Bus(sqlite, files=files) as bus:
        bus.mount_book(NotesBook)
        created = bus.publish(
            ManageBookJob(book="notes", op=BookOp.CREATE, payload={"label": "a"})
        )
        assert created.status is JobStatus.COMPLETED
        assert created.result is not None
        record_id = created.result.record["id"]
        book = bus._books["notes"]
        assert isinstance(book, NotesBook)
        path = book.path_for(record_id)
        assert path.is_file()
        assert path.parent == book.directory
        assert path.parent == tmp_path / "files" / "books.notes"


def test_bus_rejects_file_as_primary_backend(tmp_path) -> None:
    with pytest.raises(InvalidJobError, match="database backend"):
        Bus(FileBackend(tmp_path / "files"))
