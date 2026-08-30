from __future__ import annotations

from bus import Bus, FileEngine
from bus.base.BaseJob import JobStatus
from bus.firmware.books.promptsBook import PromptsBook
from bus.firmware.books.skillsBook import SkillsBook
from bus.firmware.jobs.promptJobs import (
    ResetPromptJob,
    ResetPromptJobBoard,
    SetPromptJob,
    SetPromptJobBoard,
)


def test_file_engine_creates_book_directories(tmp_path) -> None:
    files = FileEngine(tmp_path / "workspace")
    assert (files.root / "prompts").is_dir()
    assert (files.root / "skills").is_dir()


def test_file_store_reads_writes_and_deletes_within_one_book(tmp_path) -> None:
    store = FileEngine(tmp_path / "workspace").book("notes")
    assert store.write_text("a.md", "hello") is True
    assert store.directory == tmp_path / "workspace" / "notes"
    assert store.read_text("a.md") == "hello"
    assert store.file_names() == ["a.md"]
    assert store.delete_file("a.md") is True
    assert store.exists_file("a.md") is False


def test_file_store_rejects_path_escape(tmp_path) -> None:
    store = FileEngine(tmp_path / "workspace").book("notes")
    assert store.write_text("../escape.md", "no") is False
    assert store.read_text("/etc/passwd") is None
    assert store.delete_file("") is False


def test_file_store_writes_nested_names(tmp_path) -> None:
    store = FileEngine(tmp_path / "workspace").book("notes")
    assert store.write_text("agent/soul.md", "nested") is True
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
    assert book.set(key="../outside", value="bad") is False
    assert book.get(key="agent/defaults/soul") is None


def test_skills_book_seeds_packaged_defaults(tmp_path) -> None:
    files = FileEngine(tmp_path / "workspace")
    book = SkillsBook(files)
    assert "web_lookup" in book.list()
    assert book.exists("web_lookup")
    body = book.read("web_lookup")
    assert "Web 检索" in body
    assert book.read("does-not-exist") is None
    assert files.book("skills").write_text("web_lookup/SKILL.md", "operator copy") is True
    again = SkillsBook(files)
    assert again.read("web_lookup") == "operator copy"


def test_file_store_returns_false_when_target_is_a_directory(tmp_path) -> None:
    store = FileEngine(tmp_path / "workspace").book("notes")
    assert store.directory is not None
    (store.directory / "not-a-file").mkdir()
    assert store.write_text("not-a-file", "content") is False


class _FailingPromptsBook:
    def set(self, *, key: str, value: str) -> bool:
        del key, value
        return False

    def reset(self, *, key: str) -> bool:
        del key
        return False


def test_prompt_jobs_record_file_failures() -> None:
    prompts = _FailingPromptsBook()
    set_board = object.__new__(SetPromptJobBoard)
    set_board._prompts = prompts
    reset_board = object.__new__(ResetPromptJobBoard)
    reset_board._prompts = prompts

    assert set_board._execute(SetPromptJob()).status is JobStatus.FAILED
    assert reset_board._execute(ResetPromptJob()).status is JobStatus.FAILED


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
