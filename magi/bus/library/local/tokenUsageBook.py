"""TokenUsageBook — per-outbound-LLM-call billing rows.

Schema for the ``token_usage`` table.
"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import Field, Strict, StringConstraints
from sqlalchemy import (
    JSON,
    ForeignKey,
    Integer,
    String,
    select,
)
from sqlalchemy.orm import Mapped, mapped_column

from magi.bus.library.base import BaseBook, BaseRecord, BaseRecordMixin, record

# -- public dataclass ----------------------------------------------------


@record
class TokenUsage(BaseRecord):
    contact_id: Annotated[int, Strict()]
    llm_attempt_id: Annotated[str, Strict(), StringConstraints(max_length=128)] | None = None
    provider: Annotated[str, Strict(), StringConstraints(max_length=32)]
    model: Annotated[str, Strict(), StringConstraints(max_length=128)]
    input_tokens: Annotated[int, Strict(), Field(ge=0)]
    output_tokens: Annotated[int, Strict(), Field(ge=0)]
    cost_usd: Annotated[float, Strict(), Field(ge=0)] = 0.0
    extra: dict[str, Any] | None = None  # 额外上下文（缓存命中率等）


# -- internal ORM --------------------------------------------------------


class _TokenUsageRow(BaseRecordMixin):
    __tablename__ = "token_usage"

    contact_id: Mapped[int] = mapped_column(
        ForeignKey("contacts.id", ondelete="RESTRICT"), nullable=False
    )
    llm_attempt_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
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
            return [self._row_to_dto(r) for r in rows]

__all__ = ["TokenUsage", "TokenUsageBook", "_TokenUsageRow"]
