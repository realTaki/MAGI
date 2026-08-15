"""bus.library.file — file-backed Books.

Unlike the ORM-based Books in :mod:`~magi.bus.library.local` and
:mod:`~magi.bus.library.magis`, file-backed Books read/write
structured files through :class:`~magi.bus.db.file.FileShelf`.

Public surface:

- :class:`BaseFileBook` — abstract base with dunders + ``read(name)``
- :class:`PromptBook`   — typed accessors for every bundled prompt
  (soul / fallback_persona / chat_title_prompt / compaction_prompt /
  memory_block_template / contact_block_template /
  skills_block_template / daily_note_prompt /
  task_presets) plus generic ``get`` / ``get_structured`` / ``list``
  / ``exists``.
"""

from magi.bus.library.file.base import BaseFileBook
from magi.bus.library.file.promptBook import PromptBook, WorkspaceSoul

__all__ = ["BaseFileBook", "PromptBook", "WorkspaceSoul"]
