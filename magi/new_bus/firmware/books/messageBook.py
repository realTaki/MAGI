"""MessageBook — current messages.

The record type :class:`Message` is the field list for this BaseBook.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import ClassVar

from ...base.BaseBook import BaseBook
from ...base.BaseRecord import BaseRecord
from ...base.time import utcnow


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
