"""SkillsBook — workspace directories that each contain a SKILL.md."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from ...base.BaseFileBook import BaseFileBook
from ...base.file import FileEngine

_SKILL_FILE = "SKILL.md"
_NAME_RE = re.compile(r"^[a-zA-Z0-9_.\-]{1,64}$")
_DESCRIPTION_MAX = 240


@dataclass(frozen=True)
class Skill:
    """Catalog entry for one skill. The markdown body is not stored here."""

    name: str
    description: str


class SkillsBook(BaseFileBook):
    """Skill folders under ``<workspace>/skills``.

    Each skill is a directory with ``SKILL.md``. Missing packaged defaults
    are copied into the workspace once; existing operator copies are kept.

    ``list`` / ``get`` expose only ``name`` and ``description``. The markdown
    body is read on demand by ``read``.
    """

    name = "skills"
    _bundle_skills_dir: ClassVar[Path] = Path(__file__).resolve().parents[3] / "skills"

    def __init__(self, engine: FileEngine) -> None:
        super().__init__(engine)
        self._seed_defaults()

    def list(self) -> list[Skill]:
        return [skill for name in self._skill_dirs() if (skill := self.get(name)) is not None]

    def exists(self, name: str) -> bool:
        return self.get(name) is not None

    def get(self, name: str) -> Skill | None:
        loaded = self._load(name)
        return None if loaded is None else loaded[0]

    def read(self, name: str) -> str | None:
        """Return the markdown body with frontmatter stripped, or None."""
        loaded = self._load(name)
        return None if loaded is None else loaded[1]

    def _load(self, name: str) -> tuple[Skill, str] | None:
        if not _NAME_RE.match(name):
            return None
        raw = self._files.read_text(f"{name}/{_SKILL_FILE}")
        if raw is None:
            return None
        fields, body = self._parse_frontmatter(raw)
        description = (fields.get("description") or "").strip()
        if not description:
            return None
        if len(description) > _DESCRIPTION_MAX:
            description = description[: _DESCRIPTION_MAX - 1] + "…"
        return Skill(name=name, description=description), body

    def _skill_dirs(self) -> list[str]:
        return [
            name
            for name in self._files.directory_names()
            if self._files.exists_file(f"{name}/{_SKILL_FILE}")
        ]

    def _seed_defaults(self) -> None:
        source_root = self._bundle_skills_dir
        if not source_root.is_dir():
            return
        for source in source_root.iterdir():
            if not source.is_dir() or not (source / _SKILL_FILE).is_file():
                continue
            if self._files.exists_directory(source.name):
                continue
            self._files.copy_tree(source, source.name)

    @staticmethod
    def _parse_frontmatter(raw: str) -> tuple[dict[str, str], str]:
        """Split a SKILL.md into ``key: value`` frontmatter and the markdown body."""
        if not raw.startswith("---"):
            return {}, raw
        lines = raw.splitlines()
        close_idx = -1
        for index in range(1, len(lines)):
            if lines[index].strip() == "---":
                close_idx = index
                break
        if close_idx == -1:
            return {}, raw
        fields: dict[str, str] = {}
        for line in lines[1:close_idx]:
            if ":" not in line:
                continue
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip().strip("\"'")
            if key:
                fields[key] = value
        body = "\n".join(lines[close_idx + 1 :]).lstrip("\n")
        return fields, body
