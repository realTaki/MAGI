from __future__ import annotations

import pytest

from magi.new_bus import Bus, FileEngine, Slot
from magi.new_bus.base.BaseFileBook import BaseFileBook
from magi.new_bus.firmware.books.promptsBook import PromptsBook
from magi.new_bus.firmware.books.skillsBook import SkillsBook
from magi.new_bus.firmware.jobs.skillJobs import GetSkillJob


class NotesBook(BaseFileBook):
    name = "notes"


def test_file_engine_creates_book_directories(tmp_path) -> None:
    files = FileEngine(tmp_path / "workspace")
    assert (files.root / "prompts").is_dir()
    assert (files.root / "skills").is_dir()


def test_file_book_requires_a_file_engine() -> None:
    with pytest.raises(ValueError, match="FileEngine"):
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
    with pytest.raises(ValueError, match="workspace"):
        book.write("../escape.md", "no")
    with pytest.raises(ValueError, match="workspace"):
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


def test_skills_book_seeds_packaged_defaults(tmp_path) -> None:
    book = SkillsBook(FileEngine(tmp_path / "workspace"))
    assert "web_lookup" in book.list()
    assert book.exists("web_lookup")
    body = book.read("web_lookup")
    assert "Web 检索" in body
    assert book.read("does-not-exist") is None
    (book.directory / "web_lookup" / "SKILL.md").write_text("operator copy", encoding="utf-8")
    again = SkillsBook(FileEngine(tmp_path / "workspace"))
    assert again.read("web_lookup") == "operator copy"


def test_bus_opens_file_books(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    with Bus(workspace) as bus:
        assert (workspace / "prompts").is_dir()
        assert (workspace / "skills").is_dir()
        assert (workspace / "memories" / "magi.db").is_file()
        assert bus._job_boards


def test_bus_accepts_a_pathlike_workspace(tmp_path) -> None:
    with Bus(tmp_path / "workspace") as bus:
        assert bus.workspace == (tmp_path / "workspace").resolve()


def test_file_book_job_persists_external_failure(tmp_path, monkeypatch) -> None:
    with Bus(tmp_path / "workspace") as bus:
        worker = bus.for_worker("tester", (Slot(GetSkillJob, "publish"),))
        assert worker is not None
        client = worker.board(GetSkillJob)
        board = bus._job_board(GetSkillJob)
        assert board is not None

        def fail(*_args, **_kwargs):
            raise OSError("workspace is unavailable")

        monkeypatch.setattr(board, "_execute", fail)
        job_id = client.publish(GetSkillJob(name="web_lookup"))
        result = client.get_result(job_id)
        assert result is not None
        assert result.status.value == "failed"
        assert result.error == "workspace is unavailable"
