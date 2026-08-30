"""BaseFileBook — abstract base for file-backed Books.

Parallel to :class:`bus.bases.book.BaseBook` for ORM-backed
Books.  Where ``BaseBook`` subclasses wrap a SQLAlchemy session,
``BaseFileBook`` subclasses wrap a :class:`FileShelf`.

The base provides:

- A ``_shelf`` slot the subclass populates in ``__init__``;
- Pythonic dunder forwards (``__contains__``, ``__iter__``, ``__repr__``)
  so ``"soul" in book``, ``for name in book``, ``repr(book)`` all
  behave as expected;
- A ``read(name) -> str`` shortcut for "give me any markdown by name"
  — domain modules build their own semantics on top of generic file Books.

Concrete subclasses can add domain-specific helpers:

::

    class PromptBook(BaseFileBook):
        def __init__(self, shelf: FileShelf) -> None:
            super().__init__(shelf)
        def get(self, *, key: str) -> str | None: ...
        ...
"""

from __future__ import annotations

from collections.abc import Iterator

from old_bus.bases.db.file import FileShelf


class BaseFileBook:
    """Abstract base for Books backed by :class:`FileShelf`.

    Subclasses MUST call ``super().__init__(shelf)`` and SHOULD NOT
    override ``__contains__`` / ``__iter__`` / ``__repr__`` /
    :meth:`read` — those are uniform across file-backed Books.
    """

    _shelf: FileShelf

    def __init__(self, shelf: FileShelf) -> None:
        self._shelf = shelf

    def __contains__(self, name: object) -> bool:
        """``"soul" in book`` ↔ :meth:`FileShelf.exists`."""
        if not isinstance(name, str):
            return False
        return self._shelf.exists(name)

    def __iter__(self) -> Iterator[str]:
        """``for name in book`` ↔ :meth:`FileShelf.list`."""
        return iter(self._shelf.list())

    def __repr__(self) -> str:
        cls = type(self).__name__
        return f"{cls}({self._shelf!r})"

    # -- generic text read ------------------------------------------------

    def read(self, name: str) -> str:
        """Read any markdown prompt by *name* (no extension).

        Returns the stripped text content.
        """
        return self._shelf.read_text(name)


__all__ = ["BaseFileBook"]
