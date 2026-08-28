from __future__ import annotations

import pytest

from magi.new_bus import Bus, FileEngine, InvalidJobError
from magi.new_bus.base.BaseFileBook import BaseFileBook
from magi.new_bus.firmware.books.promptsBook import PromptsBook
from magi.new_bus.firmware.books.skillsBook import SkillsBook


class NotesBook(BaseFileBook):
    name = "notes"


def test_file_engine_creates_book_directories(tmp_path) -> None:
    files = FileEngine(tmp_path / "workspace")
    assert (files.root / "prompts").is_dir()
    assert (files.root / "skills").is_dir()


def test_file_book_requires_a_file_engine() -> None:
    with pytest.raises(InvalidJobError, match="FileEngine"):
        NotesBook(object())  # type: ignore[arg-type]


def test_file_book_uses_its_named_directory(tmp_path) -> None:
    book = NotesBook(FileEngine(tmp_path / "workspace"))
    path = book.write("a.md", "hello")
    assert path.is_file()
    assert path.parent == tmp_path / "workspace" / "notes"
    assert book.read("a.md") == "hello"
    assert "a.md" in book
    assert list(book) == ["a.md"]
    assert book.delete("a.md") is True
    assert "a.md" not in book


def test_file_book_rejects_path_escape(tmp_path) -> None:
    book = NotesBook(FileEngine(tmp_path / "workspace"))
    with pytest.raises(InvalidJobError, match="workspace"):
        book.write("../escape.md", "no")
    with pytest.raises(InvalidJobError, match="workspace"):
        book.read("/etc/passwd")


def test_file_book_writes_nested_names(tmp_path) -> None:
    book = NotesBook(FileEngine(tmp_path / "workspace"))
    book.write("agent/soul.md", "nested")
    assert book.read("agent/soul.md") == "nested"
    assert list(book) == ["agent/soul.md"]


def test_prompts_book_round_trip(tmp_path) -> None:
    book = PromptsBook(FileEngine(tmp_path / "workspace"))
    assert book.directory == tmp_path / "workspace" / "prompts"
    assert book.register(key="agent/soul", value="default soul") is True
    assert book.get(key="agent/soul") == "default soul"
    book.set(key="agent/soul", value="custom soul")
    assert book.get(key="agent/soul") == "custom soul"
    assert book.register(key="agent/soul", value="newer default") is False
    assert book.get(key="agent/soul") == "custom soul"
    book.reset(key="agent/soul")
    assert book.get(key="agent/soul") == "newer default"
    assert book.list() == ["agent/soul"]
    assert book.delete(key="agent/soul") is True
    assert book.get(key="agent/soul") == "newer default"


def test_skills_book_seeds_packaged_defaults(tmp_path) -> None:
    book = SkillsBook(FileEngine(tmp_path / "workspace"))
    assert "web_lookup" in book.list()
    assert book.exists("web_lookup")
    body = book.read("web_lookup")
    assert "Web 检索" in body
    (book.directory / "web_lookup" / "SKILL.md").write_text("operator copy", encoding="utf-8")
    again = SkillsBook(FileEngine(tmp_path / "workspace"))
    assert again.read("web_lookup") == "operator copy"


def test_bus_opens_file_books(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    with Bus(workspace) as bus:
        assert bus._prompts is not None
        assert bus._skills is not None
        assert bus._prompts.directory == workspace / "prompts"
        assert "web_lookup" in bus._skills.list()
        assert (workspace / "memories" / "magi.db").is_file()


def test_bus_accepts_a_pathlike_workspace(tmp_path) -> None:
    with Bus(tmp_path / "workspace") as bus:
        assert bus.workspace == (tmp_path / "workspace").resolve()
