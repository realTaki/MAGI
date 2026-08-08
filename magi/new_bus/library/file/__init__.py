"""new_bus.library.file — file-backed Books.

Unlike the ORM-based Books in :mod:`~magi.new_bus.library.local` and
:mod:`~magi.new_bus.library.magis`, file-backed Books read/write
structured files through :class:`~magi.new_bus.db.file.FileShelf`.
"""

from magi.new_bus.library.file.promptBook import PromptBook

__all__ = ["PromptBook"]
