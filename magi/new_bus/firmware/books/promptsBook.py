"""PromptsBook — workspace Markdown keyed by extensionless relative path."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Final

from ...base.BaseFileBook import BaseFileBook

KNOWN_PROMPTS: Final[dict[str, str]] = {
    "agent/soul": "Active workspace persona used for every agent turn.",
    "agent/defaults/soul": "AgentWorker upgrade-managed soul reset default.",
    "agent/chat_titles": "Active system prompt for automatic conversation titles.",
    "agent/defaults/chat_titles": "Upgrade-managed automatic-title reset default.",
    "agent/compaction": "Active system prompt for conversation compaction.",
    "agent/defaults/compaction": "Upgrade-managed compaction reset default.",
    "agent/skills_block": "Active header template for the available-skills block.",
    "agent/defaults/skills_block": "Upgrade-managed skills-block reset default.",
    "proactive/daily_standup_brief": "Daily stand-up preset prompt.",
    "proactive/weekly_review": "Weekly review preset prompt.",
    "proactive/morning_brief": "Morning brief preset prompt.",
    "proactive/night_summary": "Night summary preset prompt.",
}


def _mtime(path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).replace(tzinfo=None)


class PromptsBook(BaseFileBook):
    """Markdown key/value Book under ``<workspace>/prompts``."""

    name = "prompts"
    KNOWN_PROMPTS = KNOWN_PROMPTS

    def get(self, *, key: str) -> str | None:
        """Return active content, falling back to its managed default if absent."""
        self._require_active_key(key)
        try:
            return self._read_exact(key)
        except FileNotFoundError:
            if self._is_default_key(key):
                return None
            try:
                return self._read_exact(self._default_key(active_key=key))
            except (FileNotFoundError, ValueError):
                return None

    def set(self, *, key: str, value: str) -> datetime:
        """Atomically replace one prompt record and return its UTC mtime."""
        self._require_active_key(key)
        return self._set_exact(key=key, value=value)

    def register(self, *, key: str, value: str) -> bool:
        """Refresh a default and initialise its active record if missing."""
        self._require_active_key(key)
        self._set_exact(key=self._default_key(active_key=key), value=value)
        if self.exists(self._file_name(key)):
            return False
        self.set(key=key, value=value)
        return True

    def reset(self, *, key: str) -> datetime:
        """Replace one active prompt with its current managed default."""
        try:
            return self.set(key=key, value=self._read_exact(self._default_key(active_key=key)))
        except FileNotFoundError as error:
            raise KeyError(f"no default prompt registered for {key!r}") from error

    @staticmethod
    def _file_name(key: str) -> str:
        return key if key.endswith(".md") else f"{key}.md"

    @staticmethod
    def _default_key(*, active_key: str) -> str:
        owner, separator, relative_key = active_key.partition("/")
        if not owner or not separator or not relative_key or relative_key.startswith("defaults/"):
            raise ValueError(f"active prompt key must be '<owner>/<name>', got {active_key!r}")
        return f"{owner}/defaults/{relative_key}"

    @staticmethod
    def _is_default_key(key: str) -> bool:
        _owner, separator, relative_key = key.partition("/")
        return bool(separator and relative_key.startswith("defaults/"))

    @classmethod
    def _require_active_key(cls, key: str) -> None:
        if not isinstance(key, str) or not key.strip():
            raise ValueError("prompt key must be a non-empty relative path")
        if cls._is_default_key(key):
            raise ValueError(f"default prompt key {key!r} is managed by PromptsBook")

    def _set_exact(self, *, key: str, value: str) -> datetime:
        path = self.write(self._file_name(key), value)
        return _mtime(path)

    def _read_exact(self, key: str) -> str:
        return self.read(self._file_name(key))
