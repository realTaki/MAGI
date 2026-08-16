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

Standalone (early bootstrap / testing)::

    from magi.bus.db.file import FileShelf
    from magi.bus.library.file import PromptBook

    shelf = FileShelf("/app/magi/prompts")
    book = PromptBook(shelf)
    print(book.soul())
"""

from __future__ import annotations

import logging
import os
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from magi.bus.db.file import FileShelf
from magi.bus.library.file.base import BaseFileBook

logger = logging.getLogger("magi.bus.promptBook")


@dataclass(frozen=True, slots=True, kw_only=True)
class WorkspaceSoul:
    """Result of reading the workspace ``SOUL.md``.

    ``is_fallback`` is True when the workspace file is missing
    and the bundled fallback was returned instead.
    """

    content: str  # SOUL.md 文本内容
    mtime: datetime | None  # 文件 mtime（UTC，fallback 时为 None）
    is_fallback: bool  # True=使用了 bundled fallback


class PromptBook(BaseFileBook):
    """Typed accessors for every bundled prompt file.

    Each method reads through :class:`FileShelf`, inheriting its
    hot-reload semantics.  These are methods (not properties) so
    callers are reminded that every invocation may trigger a
    ``stat()`` + potential re-read.

    Workspace SOUL.md operations (``read_workspace_soul`` /
    ``write_workspace_soul``) use atomic file I/O directly on the
    workspace directory, not through the bundled FileShelf.
    """

    _SOUL_FILENAME = "SOUL.md"

    def __init__(self, shelf: FileShelf, *, workspace_dir: Path | None = None) -> None:
        super().__init__(shelf)
        self._workspace_dir = workspace_dir

    # -- workspace SOUL (file I/O, not bundled FileShelf) --------------------

    def _soul_path(self) -> Path:
        if self._workspace_dir is None:
            raise RuntimeError(
                "PromptBook has no workspace_dir; workspace SOUL operations are unavailable"
            )
        return self._workspace_dir / self._SOUL_FILENAME

    def read_workspace_soul(self) -> WorkspaceSoul:
        """Read the workspace ``SOUL.md``, falling back to bundled persona.

        Returns a :class:`WorkspaceSoul` with ``is_fallback=True`` and
        the bundled ``fallback_persona.md`` content when the workspace
        file is missing.
        """
        path = self._soul_path()
        try:
            content = path.read_text(encoding="utf-8").strip()
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).replace(tzinfo=None)
            return WorkspaceSoul(content=content, mtime=mtime, is_fallback=False)
        except FileNotFoundError:
            return WorkspaceSoul(
                content=self.fallback_persona(),
                mtime=None,
                is_fallback=True,
            )

    def write_workspace_soul(self, content: str) -> datetime:
        """Atomically write *content* to workspace ``SOUL.md``.

        Returns the new file's naive UTC mtime.
        """
        path = self._soul_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=str(path.parent),
            prefix=f".{path.name}.",
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_name, path)
        except Exception:
            try:
                os.unlink(tmp_name)
            except FileNotFoundError:
                pass
            raise
        return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).replace(tzinfo=None)

    # -- persona (bundled, from FileShelf) ---------------------------------

    def soul(self) -> str:
        """Return the bundled ``soul.md`` (the deployer's persona)."""
        return self._shelf.read_text("soul")

    def fallback_persona(self) -> str:
        """Return ``fallback_persona.md`` — last-resort persona.

        Used only when both the workspace's ``SOUL.md`` and the
        bundled ``soul.md`` are missing.
        """
        return self._shelf.read_text("fallback_persona")

    # -- sub-task system prompts --------------------------------------------

    def chat_title_prompt(self) -> str:
        """System prompt for the auto-title worker.

        Reads ``chat_titles.md``; used to summarise each session's
        first user message into a 3-5 word title.
        """
        return self._shelf.read_text("chat_titles")

    def compaction_prompt(self) -> str:
        """System prompt for the auto-compact worker.

        Reads ``compaction.md``.
        """
        return self._shelf.read_text("compaction")

    # -- system-prompt block templates --------------------------------------

    def memory_block_template(self) -> str:
        """The "Long-term memory" block header template.

        Reads ``context/memory_block.md``.  The static header text
        is this template; the per-entry rows are appended by
        ``format_memory_block()`` at call time.
        """
        return self._shelf.read_text("context/memory_block")

    def contact_block_template(self) -> str:
        """The "Current chatter" block template.

        Reads ``context/contact_block.md``.
        """
        return self._shelf.read_text("context/contact_block")

    def skills_block_template(self) -> str:
        """The "Available skills" block header template.

        Reads ``context/skills_block.md``.
        """
        return self._shelf.read_text("context/skills_block")

    def daily_note_prompt(self) -> str:
        """The "Daily Note 记录指令" reference document.

        Reads ``context/daily_note.md``.
        """
        return self._shelf.read_text("context/daily_note")

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
        for name in self._shelf.list("task_presets/*"):
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
