"""MessageBook — current messages.

The record type :class:`Message` is the field list for this BaseBook.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import ClassVar

from ...base.BaseBook import BaseBook, BaseRecord, utcnow
from ...base.errors import InvalidJobError

MESSAGE_ROLES = frozenset({"user", "assistant", "system", "tool"})


@dataclass(kw_only=True)
class Message(BaseRecord):
    """One row in MessageBook.

    role: ``user`` | ``assistant`` | ``system`` | ``tool``
    content: non-empty text
    conversation_id: optional Conversation.id
    timestamp: when the message was produced
    archived: hidden from the live transcript
    """

    role: str
    content: str
    conversation_id: int | None = None
    timestamp: datetime = field(default_factory=utcnow)
    archived: bool = False

    BOOK: ClassVar[str] = "messages"


class MessageBook(BaseBook):
    name = Message.BOOK
    record_cls = Message

    def _validate_write(self, record: BaseRecord) -> None:
        if not isinstance(record, Message):
            raise InvalidJobError("MessageBook only accepts Message records")
        if record.role not in MESSAGE_ROLES:
            raise InvalidJobError(
                f"message.role must be one of {sorted(MESSAGE_ROLES)}, got {record.role!r}"
            )
        if not record.content:
            raise InvalidJobError("message.content must be a non-empty string")
        if record.conversation_id is not None and (
            not isinstance(record.conversation_id, int)
            or isinstance(record.conversation_id, bool)
        ):
            raise InvalidJobError("message.conversation_id must be an integer")
        if record.conversation_id is not None and record.conversation_id < 1:
            raise InvalidJobError("message.conversation_id must be a persisted Conversation.id")
        if not isinstance(record.timestamp, datetime):
            raise InvalidJobError("message.timestamp must be a datetime")
        if not isinstance(record.archived, bool):
            raise InvalidJobError("message.archived must be a bool")
