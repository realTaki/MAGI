"""TokenUsageBook — append-only accounting records for LLM calls."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from ...base.BaseBook import BaseBook, BaseRecord, BaseRecordMixin


@dataclass(kw_only=True)
class TokenUsage(BaseRecord):
    """Usage reported for one completed LLM call.

    ``llm_job_id`` is a logical link rather than a SQL foreign key: usage is
    audit data and must remain readable even if job retention is introduced
    later.  ``contact_id`` likewise remains optional until the vNext contact
    domain exists.
    """

    llm_job_id: int
    contact_id: int | None = None
    provider: str = ""
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_hit_tokens: int = 0
    cache_miss_tokens: int = 0
    cache_write_tokens: int = 0
    thinking_tokens: int = 0
    response_tokens: int = 0


class TokenUsageRow(BaseRecordMixin):
    __tablename__ = "books_token_usage"

    llm_job_id: Mapped[int] = mapped_column(Integer, nullable=False)
    contact_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    provider: Mapped[str] = mapped_column(Text, nullable=False, default="")
    model: Mapped[str] = mapped_column(Text, nullable=False, default="")
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cache_hit_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cache_miss_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cache_write_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    thinking_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    response_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class TokenUsageBook(BaseBook[TokenUsage]):
    record_cls = TokenUsage
    row_cls = TokenUsageRow
