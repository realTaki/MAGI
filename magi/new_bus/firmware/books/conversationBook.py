"""ConversationBook — current conversations.

The record type :class:`Conversation` is the field list for this BaseBook.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from ...base.BaseBook import BaseBook, BaseRecord


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
    last_compaction_at: str | None = None

    BOOK: ClassVar[str] = "conversations"


class ConversationBook(BaseBook):
    name = Conversation.BOOK
    record_cls = Conversation
