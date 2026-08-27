"""ConversationBook — current conversations.

The record type :class:`Conversation` is the field list for this BaseBook.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import DateTime, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column

from ...base.BaseBook import BaseBook, BaseRecord, BaseRecordMixin
from ...base.time import BaseTime


@dataclass(kw_only=True)
class Conversation(BaseRecord):
    """One row in ConversationBook.

    ``contact_id`` identifies the channel-independent actor. ``channel`` and
    ``delivery_address`` identify the transport endpoint for this conversation;
    no channel-specific identity is stored on Contact.

    delivery_address: where replies go on this channel
    contact_id: participating Contact.id
    channel: transport/channel name
    title: optional display name
    summary: compacted summary
    last_compaction_at: when summary was last written
    """

    delivery_address: str
    contact_id: int
    channel: str
    title: str = ""
    summary: str = ""
    last_compaction_at: BaseTime | None = None


class ConversationRow(BaseRecordMixin):
    __tablename__ = "books_conversations"

    delivery_address: Mapped[str] = mapped_column(Text, nullable=False)
    contact_id: Mapped[int] = mapped_column(
        ForeignKey("books_contacts.id", ondelete="RESTRICT"), nullable=False
    )
    channel: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False, default="")
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    last_compaction_at: Mapped[BaseTime | None] = mapped_column(DateTime, nullable=True)


class ConversationBook(BaseBook[Conversation]):
    record_cls = Conversation
    row_cls = ConversationRow
