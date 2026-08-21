from __future__ import annotations

import pytest

from magi.new_bus import Bus, FileBackend, InvalidJobError
from magi.new_bus.base.BaseFileBook import BaseFileBook
from tests.unit.new_bus.testing import InMemoryBackend


class NotesBook(BaseFileBook):
    name = "notes"


def test_file_book_requires_a_file_backend() -> None:
    with pytest.raises(InvalidJobError, match="FileBackend"):
        NotesBook(object())


def test_file_book_uses_its_named_directory(tmp_path) -> None:
    book = NotesBook(FileBackend(tmp_path / "files"))
    path = book.write("a.md", "hello")
    assert path.is_file()
    assert path.parent == tmp_path / "files" / "notes"
    assert book.read("a.md") == "hello"
    assert "a.md" in book
    assert list(book) == ["a.md"]


def test_bus_rejects_file_as_primary_backend(tmp_path) -> None:
    with pytest.raises(InvalidJobError, match="EngineFactory"):
        Bus(FileBackend(tmp_path / "files"))  # type: ignore[arg-type]

