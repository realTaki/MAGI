"""MemoryBook — Runtime-local memories classified by retention."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from sqlalchemy import Boolean, Text
from sqlalchemy.orm import Mapped, mapped_column

from bus.base.time import utcnow

from ...base.BaseBook import BaseBook, BaseRecord, BaseRecordMixin


class MemoryKind(StrEnum):
    """How long this memory is meant to last."""

    TEMPORARY = "temporary"
    SHORT_TERM = "short_term"
    LONG_TERM = "long_term"


@dataclass(kw_only=True)
class Memory(BaseRecord):
    """One remembered item."""

    topic: str | None = field(default_factory=lambda: utcnow().strftime("%Y-%m-%d %H:%M:%S"))
    detail: str | None = "Noting in particular."
    kind: MemoryKind | None = MemoryKind.TEMPORARY
    archived: bool | None = False


class MemoryRow(BaseRecordMixin):
    __tablename__ = "books_memories"

    topic: Mapped[str] = mapped_column(Text, nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False, default="")
    kind: Mapped[str] = mapped_column(Text, nullable=False, default=MemoryKind.TEMPORARY.value)
    archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class MemoryBook(BaseBook[Memory]):
    record_cls = Memory
    row_cls = MemoryRow
