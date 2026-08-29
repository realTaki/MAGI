"""TokenUsageBook — per-outbound-LLM-call billing rows.

Schema for the ``token_usage`` table.
"""

from __future__ import annotations

import dataclasses
from typing import Any

from sqlalchemy import (
    JSON,
    ForeignKey,
    Integer,
    Text,
    select,
)
from sqlalchemy.orm import Mapped, mapped_column

from old_bus.bases.book import BaseBook, BaseRecord, BaseRecordMixin

# -- public dataclass ----------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class TokenUsage(BaseRecord):
    contact_id: int
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float = 0.0
    extra: dict[str, Any] | None = None  # 额外上下文（缓存命中率等）


# -- internal ORM --------------------------------------------------------


class _TokenUsageRow(BaseRecordMixin):
    __tablename__ = "token_usage"

    contact_id: Mapped[int] = mapped_column(
        ForeignKey("contacts.id", ondelete="RESTRICT"), nullable=False
    )
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    cost_usd: Mapped[float] = mapped_column(
        Integer,
        nullable=False,
        default=0,  # stored as micros (int) — see runner
    )
    extra: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


# -- Book ----------------------------------------------------------------


class TokenUsageBook(BaseBook[_TokenUsageRow, TokenUsage]):
    model_cls = _TokenUsageRow
    record_cls = TokenUsage

    def list_for_owner(self, *, contact_id: int) -> list[TokenUsage]:
        with self._session() as s:
            rows = s.scalars(
                select(_TokenUsageRow)
                .where(_TokenUsageRow.contact_id == contact_id)
                .order_by(_TokenUsageRow.created_at.desc())
            ).all()
            return [self.record_cls.from_row(r) for r in rows]

__all__ = ["TokenUsage", "TokenUsageBook", "_TokenUsageRow"]
