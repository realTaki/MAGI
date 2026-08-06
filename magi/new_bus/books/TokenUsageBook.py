"""TokenUsageBook — Token 用量簿。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, select
from sqlalchemy.orm import Mapped, mapped_column

from magi.new_bus.books.base import BaseBook
from magi.new_bus.db.base import Base, utcnow_naive


@dataclass(frozen=True, slots=True)
class TokenUsage:
    usage_id: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    run_id: str | None = None


class _TokenUsageRow(Base):
    __tablename__ = "token_usage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    usage_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


class TokenUsageBook(BaseBook[_TokenUsageRow, TokenUsage]):
    model_cls = _TokenUsageRow
    dto_cls = TokenUsage

    def list_by_run(self, *, run_id: str) -> list[TokenUsage]:
        with self._session() as s:
            rows = s.scalars(
                select(_TokenUsageRow)
                .where(_TokenUsageRow.run_id == run_id)
                .order_by(_TokenUsageRow.created_at)
            ).all()
            return [self._row_to_dto(r) for r in rows]

    def list_recent(self, *, limit: int = 50) -> list[TokenUsage]:
        with self._session() as s:
            rows = s.scalars(
                select(_TokenUsageRow)
                .order_by(_TokenUsageRow.created_at.desc())
                .limit(limit)
            ).all()
            return [self._row_to_dto(r) for r in rows]
