from __future__ import annotations

import pytest

from bus import Bus, FileEngine
from bus.firmware.books.promptsBook import PromptsBook
from bus.firmware.books.skillsBook import SkillsBook


def test_file_engine_creates_book_directories(tmp_path) -> None:
    files = FileEngine(tmp_path / "workspace")
    assert (files.root / "prompts").is_dir()
    assert (files.root / "skills").is_dir()


def test_file_store_reads_writes_and_deletes_within_one_book(tmp_path) -> None:
    store = FileEngine(tmp_path / "workspace").book("notes")
    path = store.write_text("a.md", "hello")
    assert path.is_file()
    assert path.parent == tmp_path / "workspace" / "notes"
    assert store.read_text("a.md") == "hello"
    assert store.file_names() == ["a.md"]
    assert store.delete_file("a.md") is True
    assert store.exists_file("a.md") is False


def test_file_store_rejects_path_escape(tmp_path) -> None:
    store = FileEngine(tmp_path / "workspace").book("notes")
    with pytest.raises(ValueError, match="workspace"):
        store.write_text("../escape.md", "no")
    with pytest.raises(ValueError, match="workspace"):
        store.read_text("/etc/passwd")


def test_file_store_writes_nested_names(tmp_path) -> None:
    store = FileEngine(tmp_path / "workspace").book("notes")
    store.write_text("agent/soul.md", "nested")
    assert store.read_text("agent/soul.md") == "nested"
    assert store.file_names() == ["agent/soul.md"]


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


def test_skills_book_seeds_packaged_defaults(tmp_path) -> None:
    files = FileEngine(tmp_path / "workspace")
    book = SkillsBook(files)
    assert "web_lookup" in book.list()
    assert book.exists("web_lookup")
    body = book.read("web_lookup")
    assert "Web 检索" in body
    assert book.read("does-not-exist") is None
    files.book("skills").write_text("web_lookup/SKILL.md", "operator copy")
    again = SkillsBook(files)
    assert again.read("web_lookup") == "operator copy"


def test_bus_opens_file_books(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    with Bus(workspace) as bus:
        assert (workspace / "prompts").is_dir()
        assert (workspace / "skills").is_dir()
        assert (workspace / "memories" / "magi.db").is_file()
        assert (workspace / "logs" / "magi.db").is_file()
        assert bus._job_boards


def test_bus_accepts_a_pathlike_workspace(tmp_path) -> None:
    with Bus(tmp_path / "workspace") as bus:
        assert bus.workspace == (tmp_path / "workspace").resolve()
