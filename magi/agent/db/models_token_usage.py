"""ORM table ``token_usage`` — one row per outbound LLM call.

Powers the per-employee token-bill aggregation endpoint
(see ``magi.channels.webui.api.employee_metrics``).
Permanent: this table is meant to grow indefinitely so
week/month aggregates stay accurate. The operator-
facing endpoint ``/api/employees/{id}/token-usage``
answers the "what did this employee cost?" question;
the session JSON files (D.6) answer the "what was
said?" question. v0 doesn't carry a separate audit
log — those two surfaces cover the same questions
the audit view would.

The four token fields follow the Anthropic SDK's
``Usage`` shape so the helper in ``agent.py`` can copy
keys verbatim. The v0 UI only renders ``input_tokens`` +
``output_tokens``; cache fields are stored so a future
dashboard view doesn't need a schema change to expose
them.

``ts`` is naive UTC (matching the convention every other
timestamp column uses). The aggregation endpoint
converts the configured timezone's
``period_start`` / ``period_end`` to UTC before issuing
the SQL — see ``_period_bounds`` in
``employee_metrics.py``. Storing tz-aware would force a
schema decision (which tz?) that the system-level
setting handles better.

``employee_id`` is NOT NULL: every chat call in v0
resolves to a concrete employee before reaching the
LLM (WebUI cookie admin + TG bound employee), so the
FK is always satisfied. If a future channel arrives
without a ``chat_id`` → ``Employee`` mapping, the
insert will surface that gap at write time rather
than silently dropping the row.

FKs reference :class:`Employee` in
:mod:`magi.agent.db.models_employee`. Type-only
import under TYPE_CHECKING — FK strings resolve at
mapper config time.
"""

from __future__ import annotations

from datetime import datetime

from magi.agent.db.base import utcnow_naive
from typing import TYPE_CHECKING

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from magi.agent.db.base import Base


if TYPE_CHECKING:
    from magi.agent.db.models_employee import Employee


class TokenUsage(Base):
    """One row per outbound LLM call."""

    __tablename__ = "token_usage"

    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(
        ForeignKey("employees.id", ondelete="CASCADE"),
        nullable=False,
    )
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str | None] = mapped_column(String(64))
    input_tokens: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    output_tokens: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    cache_creation_tokens: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    cache_read_tokens: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    ts: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, nullable=False
    )

    # Composite index — supports the aggregation
    # endpoint's ``WHERE employee_id = ? AND ts BETWEEN
    # ? AND ?``. Listed in ``__table_args__`` so it
    # gets created alongside the table by ``create_all``
    # on a fresh DB; for an existing DB,
    # ``_INDEX_MIGRATIONS`` below patches it in via
    # ``CREATE INDEX IF NOT EXISTS``.
    __table_args__ = (
        Index("ix_token_usage_emp_ts", "employee_id", "ts"),
    )

    # Read-only relationship for admin / debug views;
    # route code never traverses it to mutate the
    # employee.
    employee: Mapped["Employee"] = relationship(
        foreign_keys=[employee_id], viewonly=True
    )

    def __repr__(self) -> str:
        return (
            f"TokenUsage(id={self.id}, employee_id={self.employee_id}, "
            f"in={self.input_tokens}, out={self.output_tokens}, "
            f"ts={self.ts.isoformat()})"
        )