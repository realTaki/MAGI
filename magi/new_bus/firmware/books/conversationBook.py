"""ConversationBook — current conversations.

The record type :class:`Conversation` is the field list for this BaseBook.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar

from ...base.BaseBook import BaseBook, BaseRecord
from ...base.errors import InvalidJobError


@dataclass(kw_only=True)
class Conversation(BaseRecord):
    """One row in ConversationBook.

    delivery_address: where replies go
    contact_id: owning contact
    channel: inbound channel name
    title: optional display name
    summary: compacted summary
    last_compaction_at: when summary was last written
    """

    delivery_address: str
    contact_id: int
    channel: str
    title: str = ""
    summary: str = ""
    last_compaction_at: datetime | None = None

    BOOK: ClassVar[str] = "conversations"


class ConversationBook(BaseBook):
    name = Conversation.BOOK
    record_cls = Conversation

    def _validate_write(self, record: BaseRecord) -> None:
        if not isinstance(record, Conversation):
            raise InvalidJobError("ConversationBook only accepts Conversation records")
        if not isinstance(record.delivery_address, str):
            raise InvalidJobError("conversation.delivery_address must be a string")
        if not isinstance(record.contact_id, int) or isinstance(record.contact_id, bool):
            raise InvalidJobError("conversation.contact_id must be an integer")
        if not isinstance(record.channel, str):
            raise InvalidJobError("conversation.channel must be a string")
        if not isinstance(record.title, str):
            raise InvalidJobError("conversation.title must be a string")
        if not isinstance(record.summary, str):
            raise InvalidJobError("conversation.summary must be a string")
        if record.last_compaction_at is not None and not isinstance(
            record.last_compaction_at, datetime
        ):
            raise InvalidJobError("conversation.last_compaction_at must be a datetime")
