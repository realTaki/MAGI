"""ConvMembersBook — additional current participants in group conversations."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ...base.BaseBook import BaseBook, BaseRecord, BaseRecordMixin


@dataclass(kw_only=True)
class ConvMember(BaseRecord):
    """One current non-owner participant in a conversation."""

    conversation_id: int
    contact_id: int


class ConvMemberRow(BaseRecordMixin):
    __tablename__ = "books_conv_members"

    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("books_conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    contact_id: Mapped[int] = mapped_column(
        ForeignKey("books_contacts.id", ondelete="CASCADE"), nullable=False, index=True
    )

    __table_args__ = (
        UniqueConstraint("conversation_id", "contact_id", name="uq_books_conv_members"),
    )


class ConvMembersBook(BaseBook[ConvMember]):
    """Internal storage for non-owner conversation participants."""

    record_cls = ConvMember
    row_cls = ConvMemberRow
