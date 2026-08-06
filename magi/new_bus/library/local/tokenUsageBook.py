"""TokenUsageBook — per-outbound-LLM-call billing rows.

Schema mirrors the old bus's ``token_usage`` table.
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

from magi.new_bus.library.base import BaseBook
from magi.new_bus.db.base import Base, utcnow_naive


# -- public dataclass ----------------------------------------------------


@dataclass(frozen=True, slots=True)
class TokenUsage:
    id: int
    uid: int
    run_id: str | None
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
    uid: Mapped[int] = mapped_column(
        ForeignKey("contacts.id", ondelete="RESTRICT"), nullable=False
    )
    run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    llm_attempt_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    cost_usd: Mapped[float] = mapped_column(
        Integer, nullable=False, default=0  # stored as micros (int) — see runner
    )
    extra: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, nullable=False
    )


# -- Book ----------------------------------------------------------------


class TokenUsageBook(BaseBook[_TokenUsageRow, TokenUsage]):
    model_cls = _TokenUsageRow
    dto_cls = TokenUsage

    def list_for_run(self, *, run_id: str) -> list[TokenUsage]:
        with self._factory.session() as s:
            rows = s.scalars(
                select(_TokenUsageRow)
                .where(_TokenUsageRow.run_id == run_id)
                .order_by(_TokenUsageRow.created_at)
            ).all()
            return [self._row_to_dto(r) for r in rows]

    def list_for_owner(self, *, uid: int) -> list[TokenUsage]:
        with self._factory.session() as s:
            rows = s.scalars(
                select(_TokenUsageRow)
                .where(_TokenUsageRow.uid == uid)
                .order_by(_TokenUsageRow.created_at.desc())
            ).all()
            return [self._row_to_dto(r) for r in rows]

    def add(self, *, uid: int, provider: str, model: str,
            input_tokens: int, output_tokens: int,
            run_id: str | None = None, llm_attempt_id: str | None = None,
            cost_usd: float = 0.0,
            extra: dict[str, Any] | None = None) -> TokenUsage:
        with self._factory.session() as s:
            row = _TokenUsageRow(
                uid=uid, run_id=run_id, llm_attempt_id=llm_attempt_id,
                provider=provider, model=model,
                input_tokens=input_tokens, output_tokens=output_tokens,
                cost_usd=cost_usd, extra=extra,
            )
            s.add(row)
            s.commit()
            s.refresh(row)
        return self._row_to_dto(row)

    def sum_for_run(self, *, run_id: str) -> tuple[int, int]:
        with self._factory.session() as s:
            rows = s.scalars(
                select(_TokenUsageRow).where(_TokenUsageRow.run_id == run_id)
            ).all()
            in_total = sum(r.input_tokens for r in rows)
            out_total = sum(r.output_tokens for r in rows)
            return in_total, out_total


__all__ = ["TokenUsage", "TokenUsageBook", "_TokenUsageRow"]
