"""bus.firmwares.books.file — file-backed Books.

Unlike the ORM-based Books in :mod:`~bus.firmwares.books.local` and
:mod:`~bus.firmwares.books.magis`, file-backed Books read/write
structured files through :class:`~bus.bases.db.file.FileShelf`.

Public surface:

- :class:`PromptBook` — worker-seeded Markdown filename-to-content KV
  prompts, with :data:`KNOWN_PROMPTS` as its documented vocabulary.
"""

from old_bus.firmwares.books.file.promptBook import KNOWN_PROMPTS, PromptBook

__all__ = ["KNOWN_PROMPTS", "PromptBook"]
