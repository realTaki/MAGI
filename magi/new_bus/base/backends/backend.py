"""Backend: where BaseBook and Job records actually live."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from contextlib import AbstractContextManager
from typing import Any


class RecordStore(ABC):
    """A named collection of JSON-serializable records.

    Every record has an integer ``id``. ``0`` means unassigned: ``insert``
    lets the backend generate one. Job stores also keep ``status`` and
    ``created_at`` so claim can be implemented uniformly.
    """

    @abstractmethod
    def insert(self, record: Mapping[str, Any]) -> dict[str, Any]:
        """Insert a record. Generates ``id`` when it is 0/missing."""

    @abstractmethod
    def get(self, id: int) -> dict[str, Any] | None:
        """Return one record or ``None``."""

    @abstractmethod
    def replace(self, id: int, record: Mapping[str, Any]) -> dict[str, Any]:
        """Overwrite an existing record. Raises if it does not exist."""

    @abstractmethod
    def delete(self, id: int) -> bool:
        """Delete by id. Returns whether a record was removed."""

    @abstractmethod
    def find(
        self,
        *,
        status: str | None = None,
        eq: Mapping[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Return matching records, oldest ``created_at`` then ``id`` first."""

    @abstractmethod
    def compare_and_set(
        self,
        id: int,
        *,
        field: str,
        expect: Any,
        update: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        """Atomically apply ``update`` if ``record[field] == expect``.

        Returns the updated record, or ``None`` if the record is missing
        or the field does not match.
        """


class Backend(ABC):
    """Unified persistence handle. Base never talks to File/SQLite/Postgres directly."""

    @abstractmethod
    def ensure(self) -> None:
        """Prepare storage for this backend.

        File creates the root directory. SQLite and PostgreSQL create the
        shared ``records`` table. Safe to call more than once.
        """

    @abstractmethod
    def records(self, name: str) -> RecordStore:
        """Return the named collection, creating it if needed."""

    @abstractmethod
    def transaction(self) -> AbstractContextManager[None]:
        """All-or-nothing scope across collections on this backend."""

    @abstractmethod
    def close(self) -> None:
        """Release backend resources."""
