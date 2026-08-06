"""TokenUsageJob — writes to the ``token_usage`` table."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, select
from sqlalchemy.orm import Mapped, mapped_column

from magi.new_bus.db.engine import EngineFactory
from magi.new_bus.jobs.base import BaseJob, JobBase, job_utcnow_naive

logger = logging.getLogger("magi.new_bus.jobs.token_usage")


class _JTokenUsageRow(JobBase):
    __tablename__ = "token_usage"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    uid: Mapped[int] = mapped_column(
        ForeignKey("contacts.id", ondelete="RESTRICT"), nullable=False,
    )
    run_id: Mapped[str | None] = mapped_column(nullable=True)
    llm_attempt_id: Mapped[str | None] = mapped_column(nullable=True)
    provider: Mapped[str] = mapped_column(nullable=False)
    model: Mapped[str] = mapped_column(nullable=False)
    input_tokens: Mapped[int] = mapped_column(nullable=False)
    output_tokens: Mapped[int] = mapped_column(nullable=False)
    cost_usd: Mapped[int] = mapped_column(default=0, nullable=False)
    extra: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at = mapped_column(DateTime, default=job_utcnow_naive, nullable=False)


class TokenUsageJob(BaseJob):
    """Write side of the token-usage domain."""

    def __init__(self, factory: EngineFactory):
        super().__init__(factory)

    def add(
        self,
        *,
        uid: int,
        provider: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        run_id: str | None = None,
        llm_attempt_id: str | None = None,
        cost_usd: float = 0.0,
        extra: dict[str, Any] | None = None,
    ) -> int:
        with self._factory.session() as s:
            row = _JTokenUsageRow(
                uid=uid, run_id=run_id, llm_attempt_id=llm_attempt_id,
                provider=provider, model=model,
                input_tokens=input_tokens, output_tokens=output_tokens,
                cost_usd=cost_usd, extra=extra,
            )
            s.add(row)
            s.commit()
            s.refresh(row)
        return row.id


__all__ = ["TokenUsageJob"]
