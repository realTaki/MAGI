"""MessageBook — current messages.

The record type :class:`Message` is the field list for this Book.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, ClassVar

from ...base.book import Book
from ...errors import InvalidJobError

MESSAGE_ROLES = frozenset({"user", "assistant", "system", "tool"})


@dataclass
class Message:
    """One row in MessageBook.

    role: ``user`` | ``assistant`` | ``system`` | ``tool``
    content: non-empty text
    session_id: optional grouping key
    id / created_at / updated_at: assigned by BUS
    """

    role: str
    content: str
    session_id: str | None = None
    id: str = ""
    created_at: str | None = None
    updated_at: str | None = None

    BOOK: ClassVar[str] = "messages"


class MessageBook(Book):
    NAME = Message.BOOK
    record_cls = Message

    def __init__(self, name: str, backend) -> None:
        if name != self.NAME:
            raise InvalidJobError(f"MessageBook must be named {self.NAME!r}")
        super().__init__(name, backend)

    def _validate_write(self, record: Mapping[str, Any]) -> None:
        role = record.get("role")
        if role not in MESSAGE_ROLES:
            raise InvalidJobError(
                f"message.role must be one of {sorted(MESSAGE_ROLES)}, got {role!r}"
            )
        content = record.get("content")
        if not isinstance(content, str) or not content:
            raise InvalidJobError("message.content must be a non-empty string")
        session_id = record.get("session_id")
        if session_id is not None and not isinstance(session_id, str):
            raise InvalidJobError("message.session_id must be a string")
