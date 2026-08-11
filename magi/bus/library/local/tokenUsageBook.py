"""TokenUsageBook — per-outbound-LLM-call billing rows.

Schema for the ``token_usage`` table.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Integer,
    String,
    select,
)
from sqlalchemy.orm import Mapped, mapped_column

from magi.bus.db.base import Base, utcnow_naive
from magi.bus.library.base import BaseBook

# -- public dataclass ----------------------------------------------------


@dataclass(frozen=True, slots=True)
class TokenUsage:
    id: int
    contact_id: int
    llm_attempt_id: str | None
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    extra: dict[str, Any] | None = None
    created_at: datetime | None = None


# -- internal ORM --------------------------------------------------------


class _TokenUsageRow(Base):
    __tablename__ = "token_usage"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
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
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, nullable=False)


# -- Book ----------------------------------------------------------------


class TokenUsageBook(BaseBook[_TokenUsageRow, TokenUsage]):
    model_cls = _TokenUsageRow
    dto_cls = TokenUsage

    def list_for_owner(self, *, contact_id: int) -> list[TokenUsage]:
        with self._session() as s:
            rows = s.scalars(
                select(_TokenUsageRow)
                .where(_TokenUsageRow.contact_id == contact_id)
                .order_by(_TokenUsageRow.created_at.desc())
            ).all()
            return [self._row_to_dto(r) for r in rows]

    def add(
        self,
        *,
        contact_id: int,
        provider: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        llm_attempt_id: str | None = None,
        cost_usd: float = 0.0,
        extra: dict[str, Any] | None = None,
    ) -> TokenUsage:
        with self._session() as s:
            row = _TokenUsageRow(
                contact_id=contact_id,
                llm_attempt_id=llm_attempt_id,
                provider=provider,
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=cost_usd,
                extra=extra,
            )
            s.add(row)
            s.commit()
            s.refresh(row)
        return self._row_to_dto(row)


__all__ = ["TokenUsage", "TokenUsageBook", "_TokenUsageRow"]
