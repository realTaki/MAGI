from __future__ import annotations

from bus import Bus, FileEngine, GetSkillJob, ListSkillsJob
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
    assert store.write_text("nested/file.md", "nested") is True
    assert store.read_text("nested/file.md") == "nested"
    assert store.file_names() == ["nested/file.md"]


def test_prompts_book_round_trip(tmp_path) -> None:
    book = PromptsBook(FileEngine(tmp_path / "workspace"))
    assert book.directory == tmp_path / "workspace" / "prompts"
    assert book.register(key="agent/AGENT", value="default persona") is True
    assert book.get(key="agent/AGENT") == "default persona"
    book.set(key="agent/AGENT", value="custom persona")
    assert book.get(key="agent/AGENT") == "custom persona"
    assert book.register(key="agent/AGENT", value="newer default") is True
    assert book.get(key="agent/AGENT") == "custom persona"
    book.reset(key="agent/AGENT")
    assert book.get(key="agent/AGENT") == "newer default"
    assert book.set(key="../outside", value="bad") is False
    assert book.get(key="agent/defaults/AGENT") is None


def test_skills_book_seeds_packaged_defaults(tmp_path) -> None:
    files = FileEngine(tmp_path / "workspace")
    book = SkillsBook(files)
    listed = {skill.name: skill.description for skill in book.list()}
    assert "web_lookup" in listed
    assert listed["web_lookup"]
    assert book.exists("web_lookup")
    body = book.read("web_lookup")
    assert body is not None
    assert "Web 检索" in body
    assert "name: web_lookup" not in body
    assert book.read("does-not-exist") is None
    operator = (
        "---\nname: web_lookup\ndescription: operator copy\n---\n\noperator body\n"
    )
    assert files.book("skills").write_text("web_lookup/SKILL.md", operator) is True
    again = SkillsBook(files)
    assert again.get("web_lookup") is not None
    assert again.get("web_lookup").description == "operator copy"
    assert again.read("web_lookup") == "operator body"


def test_skills_book_skips_entries_without_description(tmp_path) -> None:
    files = FileEngine(tmp_path / "workspace")
    store = files.book("skills")
    assert store.write_text("nodesc/SKILL.md", "---\nname: nodesc\n---\n\nbody\n") is True
    book = SkillsBook(files)
    assert book.get("nodesc") is None
    assert book.read("nodesc") is None
    assert all(skill.name != "nodesc" for skill in book.list())


def test_file_store_returns_false_when_target_is_a_directory(tmp_path) -> None:
    store = FileEngine(tmp_path / "workspace").book("notes")
    (store.directory / "not-a-file").mkdir()
    assert store.write_text("not-a-file", "content") is False


class _FailingPromptsBook:
    def set(self, *, key: str, value: str) -> bool:
        del key, value
        return False

    def reset(self, *, key: str) -> bool:
        del key
        return False


def test_skill_jobs_list_catalog_and_read_body(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    with Bus("@skills", workspace=workspace) as bus:
        listed = bus.board(ListSkillsJob)
        fetched = bus.board(GetSkillJob)
        assert listed is not None and fetched is not None
        catalog = listed.publish(ListSkillsJob(publisher="test"))
        assert catalog.skills
        names = {skill.name: skill.description for skill in catalog.skills}
        assert "web_lookup" in names
        assert names["web_lookup"]
        body = fetched.publish(GetSkillJob(publisher="test", name="web_lookup"))
        assert body.content is not None
        assert "Web 检索" in body.content
        assert "name: web_lookup" not in body.content
        missing = fetched.publish(GetSkillJob(publisher="test", name="does-not-exist"))
        assert missing.content is None


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
