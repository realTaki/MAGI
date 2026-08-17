"""bus.library.file — file-backed Books.

Unlike the ORM-based Books in :mod:`~magi.bus.library.local` and
:mod:`~magi.bus.library.magis`, file-backed Books read/write
structured files through :class:`~magi.bus.db.file.FileShelf`.

Public surface:

- :class:`BaseFileBook` — abstract base with dunders + ``read(name)``
- :class:`PromptBook`   — worker-seeded Markdown filename-to-content KV
  prompts, with :data:`KNOWN_PROMPTS` as its documented vocabulary.
"""

from magi.bus.library.file.base import BaseFileBook
from magi.bus.library.file.promptBook import KNOWN_PROMPTS, PromptBook

__all__ = ["BaseFileBook", "KNOWN_PROMPTS", "PromptBook"]
