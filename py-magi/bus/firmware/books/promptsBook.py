"""PromptsBook — workspace Markdown keyed by extensionless relative path."""

from __future__ import annotations

from typing import Final

from ...base.BaseFileBook import BaseFileBook

KNOWN_PROMPTS: Final[dict[str, str]] = {
    "agent/AGENT": "Active workspace persona used for every agent turn.",
    "agent/defaults/AGENT": "AgentWorker upgrade-managed AGENT.md reset default.",
    "agent/compaction": "Active system prompt for conversation compaction.",
    "agent/defaults/compaction": "Upgrade-managed compaction reset default.",
    "agent/skills_block": "Active header template for the available-skills block.",
    "agent/defaults/skills_block": "Upgrade-managed skills-block reset default.",
    "proactive/daily_standup_brief": "Daily stand-up preset prompt.",
    "proactive/weekly_review": "Weekly review preset prompt.",
    "proactive/morning_brief": "Morning brief preset prompt.",
    "proactive/night_summary": "Night summary preset prompt.",
}


class PromptsBook(BaseFileBook):
    """Markdown key/value Book under ``<workspace>/prompts``."""

    name = "prompts"
    KNOWN_PROMPTS = KNOWN_PROMPTS

    def get(self, *, key: str) -> str | None:
        """Return active content, falling back to its managed default if absent."""
        if not self._is_active_key(key):
            return None
        active = self._read_exact(key)
        if active is not None:
            return active
        default_key = self._default_key(active_key=key)
        return None if default_key is None else self._read_exact(default_key)

    def set(self, *, key: str, value: str) -> bool:
        """Atomically replace one prompt record."""
        if not self._is_active_key(key):
            return False
        return self._set_exact(key=key, value=value)

    def register(self, *, key: str, value: str) -> bool:
        """Refresh a default and initialise its active record if missing."""
        default_key = self._default_key(active_key=key)
        if default_key is None or not self._set_exact(key=default_key, value=value):
            return False
        if self._files.exists_file(self._file_name(key)):
            return True
        return self.set(key=key, value=value)

    def reset(self, *, key: str) -> bool:
        """Replace one active prompt with its current managed default."""
        default_key = self._default_key(active_key=key)
        if default_key is None:
            return False
        value = self._read_exact(default_key)
        return value is not None and self.set(key=key, value=value)

    @staticmethod
    def _file_name(key: str) -> str:
        return key if key.endswith(".md") else f"{key}.md"

    @staticmethod
    def _default_key(*, active_key: str) -> str | None:
        owner, separator, relative_key = active_key.partition("/")
        if not owner or not separator or not relative_key or relative_key.startswith("defaults/"):
            return None
        return f"{owner}/defaults/{relative_key}"

    @staticmethod
    def _is_default_key(key: str) -> bool:
        _owner, separator, relative_key = key.partition("/")
        return bool(separator and relative_key.startswith("defaults/"))

    @classmethod
    def _is_active_key(cls, key: object) -> bool:
        return isinstance(key, str) and bool(key.strip()) and not cls._is_default_key(key)

    def _set_exact(self, *, key: str, value: str) -> bool:
        return self._files.write_text(self._file_name(key), value)

    def _read_exact(self, key: str) -> str | None:
        return self._files.read_text(self._file_name(key))
