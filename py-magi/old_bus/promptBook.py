"""PromptBook — workspace-managed filename-to-content prompt store.

Prompt files are durable BUS records rooted at ``<workspace>/prompts``.
Their key is the extensionless relative filename and their value is Markdown
content; for example ``agent/soul`` maps to ``agent/soul.md``.  The Book does
not interpret prompt text or import owner modules.  Each Worker owns its
defaults and registers missing records during startup.

``KNOWN_PROMPTS`` is a developer-facing inventory, mirroring
``SettingBook.KNOWN_KEYS``. It is informative rather than restrictive:
operators and modules may add further prompt keys through this Book.
"""

from __future__ import annotations

from datetime import datetime
from typing import Final

from old_bus.bases.db.file import FileShelf

KNOWN_PROMPTS: Final[dict[str, str]] = {
    # AgentWorker-owned prompts.
    "agent/soul": "Active workspace persona used for every agent turn.",
    "agent/defaults/soul": "AgentWorker upgrade-managed soul reset default.",
    "agent/chat_titles": "Active system prompt for automatic conversation titles.",
    "agent/defaults/chat_titles": "Upgrade-managed automatic-title reset default.",
    "agent/compaction": "Active system prompt for conversation compaction.",
    "agent/defaults/compaction": "Upgrade-managed compaction reset default.",
    "agent/skills_block": "Active header template for the available-skills block.",
    "agent/defaults/skills_block": "Upgrade-managed skills-block reset default.",
    # ProactiveWorker-owned prompts. Markdown front matter carries each
    # preset's schedule metadata; its body is the task prompt.
    "proactive/daily_standup_brief": "Daily stand-up preset prompt.",
    "proactive/weekly_review": "Weekly review preset prompt.",
    "proactive/morning_brief": "Morning brief preset prompt.",
    "proactive/night_summary": "Night summary preset prompt.",
}


class PromptBook:
    """Markdown key/value Book backed by :class:`FileShelf`.

    Keys are extensionless relative filenames. All values are strings and are
    stored as ``.md`` files, preserving FileShelf's atomic-write and
    hot-reload behaviour.
    """

    KNOWN_PROMPTS = KNOWN_PROMPTS

    def __init__(self, shelf: FileShelf) -> None:
        self._shelf = shelf

    def get(self, *, key: str) -> str | None:
        """Return active content, falling back to its managed default if absent."""
        self._require_active_key(key)
        try:
            return self._read_exact(key)
        except FileNotFoundError:
            if self._is_default_key(key):
                return None
            try:
                default_key = self._default_key(active_key=key)
            except ValueError:
                return None
            try:
                return self._read_exact(default_key)
            except FileNotFoundError:
                return None

    def set(self, *, key: str, value: str) -> datetime:
        """Atomically replace one prompt record and return its UTC mtime."""
        self._require_active_key(key)
        return self._set_exact(key=key, value=value)

    @staticmethod
    def _default_key(*, active_key: str) -> str:
        """Return the upgrade-managed default record for an active prompt key.

        ``agent/skills_block`` becomes ``agent/defaults/skills_block``. Keeping
        the owner namespace
        first makes default and active records co-locate in one module tree.
        """
        owner, separator, relative_key = active_key.partition("/")
        if not owner or not separator or not relative_key or relative_key.startswith("defaults/"):
            raise ValueError(f"active prompt key must be '<owner>/<name>', got {active_key!r}")
        return f"{owner}/defaults/{relative_key}"

    @staticmethod
    def _is_default_key(key: str) -> bool:
        """Whether *key* is already a managed default path."""
        _owner, separator, relative_key = key.partition("/")
        return bool(separator and relative_key.startswith("defaults/"))

    @classmethod
    def _require_active_key(cls, key: str) -> None:
        """Reject default paths from the public active-prompt API."""
        if cls._is_default_key(key):
            raise ValueError(f"default prompt key {key!r} is managed by PromptBook")

    def register(self, *, key: str, value: str) -> bool:
        """Refresh a default and initialise its active record if missing.

        Worker startup calls this for every package-owned prompt. The default
        is always replaced so package upgrades improve future resets; the
        active record is written only once so an operator's customisation is
        never overwritten. Returns whether this call created the active key.
        """
        self._require_active_key(key)
        self._set_exact(key=self._default_key(active_key=key), value=value)
        if self._shelf.exists(key):
            return False
        self.set(key=key, value=value)
        return True

    def reset(self, *, key: str) -> datetime:
        """Replace one active prompt with its current managed default."""
        default_key = self._default_key(active_key=key)
        try:
            return self.set(key=key, value=self._read_exact(default_key))
        except FileNotFoundError as exc:
            raise KeyError(f"no default prompt registered for {key!r}") from exc

    def delete(self, *, key: str) -> bool:
        """Delete one prompt record, returning whether it existed."""
        self._require_active_key(key)
        if not self._shelf.exists(key):
            return False
        self._shelf.delete(key)
        return True

    def list(self) -> list[str]:
        """List active prompt keys; internal default records are hidden."""
        return [key for key in self._shelf.list() if not self._is_default_key(key)]

    def _set_exact(self, *, key: str, value: str) -> datetime:
        """Replace an exact physical record, including an internal default."""
        self._shelf.write_text(key, value)
        return self._shelf.modified_at(key)

    def _read_exact(self, key: str) -> str:
        """Read an exact physical record without applying active fallback."""
        return self._shelf.read_text(key)


__all__ = ["KNOWN_PROMPTS", "PromptBook"]
