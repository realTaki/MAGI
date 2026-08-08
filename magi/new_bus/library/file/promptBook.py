"""PromptBook — read MAGI prompt assets from a file directory.

Built on :class:`~magi.new_bus.db.file.FileShelf`, ``PromptBook``
provides typed accessors for every prompt file the runtime emits:

- ``soul.md``, ``fallback_persona.md`` — system-prompt persona
- ``chat_titles.md``, ``compaction.md`` — sub-task system prompts
- ``context/*.md`` — system-prompt block templates
- ``bot_replies.yaml`` — Telegram bot reply templates

Every accessor method triggers a hot-reload check (single ``stat()``
per call), so editing a prompt file takes effect on the next LLM
turn without restarting the process.

Usage via ``NewBus``::

    bus = bootstrap_new_bus(...)
    soul = bus.prompt_book.soul()
    replies = bus.prompt_book.bot_replies()

Standalone (early bootstrap / testing)::

    from magi.new_bus.db.file import FileShelf
    from magi.new_bus.library.file import PromptBook

    shelf = FileShelf("/app/magi/prompts")
    book = PromptBook(shelf)
    print(book.soul())
"""

from __future__ import annotations

from typing import Any

from magi.new_bus.db.file import FileShelf


class PromptBook:
    """Typed accessors for every bundled prompt file.

    Each method reads through :class:`FileShelf`, inheriting its
    hot-reload semantics.  These are methods (not properties) so
    callers are reminded that every invocation may trigger a
    ``stat()`` + potential re-read.
    """

    def __init__(self, shelf: FileShelf) -> None:
        self._shelf = shelf

    # -- persona ------------------------------------------------------------

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

        Reads ``context/daily_note.md``.  NOT auto-injected into
        every turn — the operator toggles ``system.show_daily_note_prompt``.
        """
        return self._shelf.read_text("context/daily_note")

    # -- structured templates -----------------------------------------------

    def bot_replies(self) -> dict[str, str]:
        """Return Telegram bot reply templates as ``{template_id: text}``.

        Reads and parses ``bot_replies.yaml``.  Values use
        ``str.format()`` placeholders; callers interpolate.
        """
        data = self._shelf.read("bot_replies")
        if not isinstance(data, dict):
            raise ValueError(
                f"bot_replies.yaml must be a mapping; got {type(data).__name__}"
            )
        out: dict[str, str] = {}
        for key, value in data.items():
            if not isinstance(value, str):
                raise ValueError(
                    f"bot_replies.yaml key {key!r} is not a string template"
                )
            out[key] = value
        return out

    # -- generic access (for future prompts not yet typed) ------------------

    def get(self, name: str) -> str:
        """Read any markdown prompt by *name* (no extension)."""
        return self._shelf.read_text(name)

    def get_structured(self, name: str) -> dict[str, Any] | list[Any]:
        """Read and decode any YAML/JSON prompt by *name*."""
        data = self._shelf.read(name)
        if not isinstance(data, (dict, list)):
            raise TypeError(
                f"Expected dict or list for {name!r}, got {type(data).__name__}"
            )
        return data

    def list(self) -> list[str]:
        """List all available prompt names."""
        return self._shelf.list()

    def exists(self, name: str) -> bool:
        """Return ``True`` if a prompt file for *name* exists."""
        return self._shelf.exists(name)
