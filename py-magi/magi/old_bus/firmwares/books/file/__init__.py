"""bus.firmwares.books.file — file-backed Books.

Unlike the ORM-based Books in :mod:`~magi.bus.firmwares.books.local` and
:mod:`~magi.bus.firmwares.books.magis`, file-backed Books read/write
structured files through :class:`~magi.bus.bases.db.file.FileShelf`.

Public surface:

- :class:`PromptBook` — worker-seeded Markdown filename-to-content KV
  prompts, with :data:`KNOWN_PROMPTS` as its documented vocabulary.
"""

from magi.old_bus.firmwares.books.file.promptBook import KNOWN_PROMPTS, PromptBook

__all__ = ["KNOWN_PROMPTS", "PromptBook"]
