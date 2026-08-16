"""PromptBook — read MAGI prompt assets from a file directory.

Built on :class:`~magi.bus.db.file.FileShelf`, ``PromptBook``
provides typed accessors for every prompt file the runtime emits:

- ``soul.md``, ``fallback_persona.md`` — system-prompt persona
- ``chat_titles.md``, ``compaction.md`` — sub-task system prompts
- ``context/*.md`` — system-prompt block templates
- ``task_presets/*.yaml`` — bundled proactive task templates
  (keyed by ``key`` field)

Every accessor method triggers a hot-reload check (single ``stat()``
per call), so editing a prompt file takes effect on the next LLM
turn without restarting the process.

Usage via ``Bus``::

    bus = open_bus(...)
    soul = bus.prompt_book.soul()
    presets = bus.prompt_book.task_presets()

Standalone (testing)::

    from magi.bus.db.file import FileShelf
    from magi.bus.library.file import PromptBook

    shelf = FileShelf("/workspace/prompts")
    book = PromptBook(shelf)
    print(book.soul())
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from magi.bus.db.file import FileShelf
from magi.bus.library.file.base import BaseFileBook

logger = logging.getLogger("magi.bus.promptBook")


@dataclass(frozen=True, slots=True, kw_only=True)
class WorkspaceSoul:
    """Result of reading the managed Agent persona.

    ``is_fallback`` is True when the workspace file is missing
    and the bundled fallback was returned instead.
    """

    content: str
    mtime: datetime | None  # 文件 mtime（UTC，fallback 时为 None）
    is_fallback: bool  # True=使用了 bundled fallback


class PromptBook(BaseFileBook):
    """Typed accessors for workspace-managed prompt files.

    Each method reads through :class:`FileShelf`, inheriting its
    hot-reload semantics.  These are methods (not properties) so
    callers are reminded that every invocation may trigger a
    ``stat()`` + potential re-read.

    Each owning Worker seeds its defaults through :meth:`ensure` during
    startup.  All later reads and writes stay inside this Book.
    """

    def ensure(self, name: str, value: Any, *, suffix: str) -> bool:
        """Write a module default only when its managed record is absent."""
        if self._shelf.exists(name):
            return False
        self._shelf.write(name, value, suffix=suffix)
        return True

    def read_workspace_soul(self) -> WorkspaceSoul:
        """Read the managed node persona, falling back to generic persona.

        Returns a :class:`WorkspaceSoul` with ``is_fallback=True`` and
        the generic fallback content when the managed record is missing.
        """
        try:
            content = self._shelf.read_text("agent/soul")
            mtime = self._shelf.modified_at("agent/soul")
            return WorkspaceSoul(content=content, mtime=mtime, is_fallback=False)
        except FileNotFoundError:
            return WorkspaceSoul(
                content=self.fallback_persona(),
                mtime=None,
                is_fallback=True,
            )

    def write_workspace_soul(self, content: str) -> datetime:
        """Atomically write *content* to the managed node persona.

        Returns the new file's naive UTC mtime.
        """
        self._shelf.write_text("agent/soul", content)
        return self._shelf.modified_at("agent/soul")

    # -- agent prompts ------------------------------------------------------

    def soul(self) -> str:
        """Return the active node persona."""
        try:
            return self._shelf.read_text("agent/soul")
        except FileNotFoundError:
            return self.default_soul()

    def default_soul(self) -> str:
        """Return the Agent Worker's immutable seed persona."""
        return self._shelf.read_text("agent/defaults/soul")

    def fallback_persona(self) -> str:
        """Return ``fallback_persona.md`` — last-resort persona.

        Used only when the active node persona is missing.
        """
        return self._shelf.read_text("agent/defaults/fallback_persona")

    # -- sub-task system prompts --------------------------------------------

    def chat_title_prompt(self) -> str:
        """System prompt for the auto-title worker.

        Reads ``chat_titles.md``; used to summarise each session's
        first user message into a 3-5 word title.
        """
        return self._shelf.read_text("agent/chat_titles")

    def compaction_prompt(self) -> str:
        """System prompt for the auto-compact worker.

        Reads ``compaction.md``.
        """
        return self._shelf.read_text("agent/compaction")

    # -- system-prompt block templates --------------------------------------

    def skills_block_template(self) -> str:
        """The "Available skills" block header template.

        Reads ``context/skills_block.md``.
        """
        return self._shelf.read_text("agent/context/skills_block")

    # -- task presets -------------------------------------------------------

    def task_presets(self) -> dict[str, dict[str, Any]]:
        """Bundled proactive task templates, keyed by ``preset.key``.

        Reads every YAML file under ``task_presets/`` and merges their
        top-level ``presets:`` lists into one ``{key: preset_dict}``
        mapping.  Collisions (same ``key`` in multiple files) keep the
        last file's entry — file order is the shelf's ``list()`` order
        (sorted by stem).

        Each preset dict carries the YAML schema used by
        bus Book API / the bundled
        ``defaults.yaml``: ``id``, ``key``, ``name``, ``description``,
        ``prompt``, ``frequency``, ``hour``, ``minute``,
        ``day_of_week``, ``day_of_month``, ``run_at``, ``channel``,
        ``enabled``.
        """
        out: dict[str, dict[str, Any]] = {}
        for name in self._shelf.list("proactive/task_presets/*"):
            data = self._shelf.read(name)
            if not isinstance(data, dict):
                raise ValueError(
                    f"task preset file {name!r} must be a mapping; got {type(data).__name__}"
                )
            presets_list = data.get("presets")
            if not isinstance(presets_list, list):
                raise ValueError(f"task preset file {name!r} missing 'presets' list")
            for preset in presets_list:
                if not isinstance(preset, dict) or "key" not in preset:
                    raise ValueError(f"task preset file {name!r} contains entry without 'key'")
                out[preset["key"]] = preset
        return out

    # -- generic access (for future prompts not yet typed) ------------------

    def get_structured(self, name: str) -> dict[str, Any] | list[Any]:
        """Read and decode any YAML/JSON prompt by *name*."""
        data = self._shelf.read(name)
        if not isinstance(data, (dict, list)):
            raise TypeError(f"Expected dict or list for {name!r}, got {type(data).__name__}")
        return data

    def list(self) -> list[str]:
        """List all available prompt names (delegates to :meth:`FileShelf.list`)."""
        return self._shelf.list()

    def exists(self, name: str) -> bool:
        """Return ``True`` if a prompt file for *name* exists."""
        return self._shelf.exists(name)
