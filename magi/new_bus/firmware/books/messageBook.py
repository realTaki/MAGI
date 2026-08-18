"""MessageBook — current messages. Internal to Firmware."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ...base.book import Book
from ...errors import InvalidJobError

MESSAGE_ROLES = frozenset({"user", "assistant", "system", "tool"})


class MessageBook(Book):
    NAME = "messages"

    def __init__(self, name: str, backend) -> None:
        if name != self.NAME:
            raise InvalidJobError(f"MessageBook must be mounted as {self.NAME!r}")
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
