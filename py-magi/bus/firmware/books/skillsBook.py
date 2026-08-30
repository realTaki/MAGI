"""SkillsBook — workspace directories that each contain a SKILL.md."""

from __future__ import annotations

from pathlib import Path

from ...base.BaseFileBook import BaseFileBook
from ...base.file import FileEngine

_SKILL_FILE = "SKILL.md"


def _bundle_skills_dir() -> Path:
    try:
        import bus

        candidate = Path(bus.__file__).resolve().parent.parent / "skills"
        if candidate.is_dir():
            return candidate
    except Exception:
        pass
    return Path(__file__).resolve().parents[3] / "skills"


class SkillsBook(BaseFileBook):
    """Skill folders under ``<workspace>/skills``.

    Each skill is a directory with ``SKILL.md``. Missing packaged defaults
    are copied into the workspace once; existing operator copies are kept.
    """

    name = "skills"

    def __init__(self, engine: FileEngine) -> None:
        super().__init__(engine)
        self._seed_defaults()

    def list(self) -> list[str]:
        return sorted(self._skill_dirs())

    def exists(self, name: str) -> bool:
        return self._files.exists_file(f"{name}/{_SKILL_FILE}")

    def read(self, name: str) -> str | None:
        try:
            return self._files.read_text(f"{name}/{_SKILL_FILE}")
        except (FileNotFoundError, ValueError):
            return None

    def _skill_dirs(self) -> list[str]:
        return [
            name
            for name in self._files.directory_names()
            if self._files.exists_file(f"{name}/{_SKILL_FILE}")
        ]

    def _seed_defaults(self) -> None:
        source_root = _bundle_skills_dir()
        if not source_root.is_dir():
            return
        for source in source_root.iterdir():
            if not source.is_dir() or not (source / _SKILL_FILE).is_file():
                continue
            if self._files.exists_directory(source.name):
                continue
            self._files.copy_tree(source, source.name)
