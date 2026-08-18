"""ConversationBook — current conversations.

The record type :class:`Conversation` is the field list for this BaseBook.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from ...base.BaseBook import BaseBook, BaseRecord
from ...base.errors import InvalidJobError


@dataclass(kw_only=True)
class Conversation(BaseRecord):
    """One row in ConversationBook.

    title: optional display name
    """

    title: str = ""

    BOOK: ClassVar[str] = "conversations"


class ConversationBook(BaseBook):
    NAME = Conversation.BOOK
    record_cls = Conversation

    def __init__(self, name: str, backend) -> None:
        if name != self.NAME:
            raise InvalidJobError(f"ConversationBook must be named {self.NAME!r}")
        super().__init__(name, backend)

    def _validate_write(self, record: BaseRecord) -> None:
        if not isinstance(record, Conversation):
            raise InvalidJobError("ConversationBook only accepts Conversation records")
        if not isinstance(record.title, str):
            raise InvalidJobError("conversation.title must be a string")
