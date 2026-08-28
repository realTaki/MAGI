"""SkillsBook — workspace directories that each contain a SKILL.md."""

from __future__ import annotations

import shutil
from pathlib import Path

from ...base.errors import InvalidJobError
from ...base.file import FileEngine, resolve_under

_SKILL_FILE = "SKILL.md"


def _bundle_skills_dir() -> Path:
    try:
        import magi

        candidate = Path(magi.__file__).resolve().parent / "skills"
        if candidate.is_dir():
            return candidate
    except Exception:
        pass
    return Path(__file__).resolve().parents[3] / "skills"


class SkillsBook:
    """Skill folders under ``<workspace>/skills``.

    Each skill is a directory with ``SKILL.md``. Missing packaged defaults
    are copied into the workspace once; existing operator copies are kept.
    """

    name = "skills"

    def __init__(self, engine: FileEngine) -> None:
        if not isinstance(engine, FileEngine):
            raise InvalidJobError("SkillsBook requires FileEngine")
        self._root = engine.directory(self.name)
        self._seed_defaults()

    @property
    def directory(self) -> Path:
        return self._root

    def list(self) -> list[str]:
        return sorted(self._skill_dirs())

    def exists(self, name: str) -> bool:
        try:
            return self._skill_file(name).is_file()
        except InvalidJobError:
            return False

    def read(self, name: str) -> str | None:
        try:
            path = self._skill_file(name)
        except InvalidJobError:
            return None
        if not path.is_file():
            return None
        return path.read_text(encoding="utf-8")

    def _skill_dirs(self) -> list[str]:
        if not self._root.is_dir():
            return []
        names: list[str] = []
        for path in self._root.iterdir():
            if path.is_dir() and (path / _SKILL_FILE).is_file():
                names.append(path.name)
        return names

    def _skill_file(self, name: str) -> Path:
        return resolve_under(self._root, name) / _SKILL_FILE

    def _seed_defaults(self) -> None:
        source_root = _bundle_skills_dir()
        if not source_root.is_dir():
            return
        for source in source_root.iterdir():
            if not source.is_dir() or not (source / _SKILL_FILE).is_file():
                continue
            try:
                target = resolve_under(self._root, source.name)
            except InvalidJobError:
                continue
            if target.exists():
                continue
            shutil.copytree(source, target)
