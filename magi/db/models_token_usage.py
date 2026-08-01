
"""ORM table ``token_usage`` — one row per outbound LLM call.

Powers the per-contact token-bill aggregation endpoint
(see ``magi.channels.webui.api.token_metrics``).
Permanent: this table is meant to grow indefinitely so
week/month aggregates stay accurate. The operator-
facing endpoint ``/api/contacts/{uid}/token-usage`` 
answers the "what did this contact cost?" question;
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
``contact_metrics.py``. Storing tz-aware would force a
schema decision (which tz?) that the system-level
setting handles better.

``uid`` is NOT NULL: every chat call in v0
resolves to a concrete contact before reaching the
LLM (WebUI cookie admin + TG bound contact), so the
FK is always satisfied. If a future channel arrives
without a ``uid`` → ``Contact`` mapping, the
insert will surface that gap at write time rather
than silently dropping the row.

FKs reference :class:`Contact` in
:mod:`magi.db.models_contact`. Type-only
import under TYPE_CHECKING — FK strings resolve at
mapper config time.
"""

from __future__ import annotations

from datetime import datetime

from magi.db.base import utcnow_naive
from typing import TYPE_CHECKING

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from magi.db.base import Base


if TYPE_CHECKING:
    from magi.db.models_contact import Contact


class TokenUsage(Base):
    """One row per outbound LLM call."""

    __tablename__ = "token_usage"

    id: Mapped[int] = mapped_column(primary_key=True)
    uid: Mapped[int] = mapped_column(
        ForeignKey("contacts.id", ondelete="CASCADE"),
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
    # endpoint's ``WHERE uid = ? AND ts BETWEEN
    # ? AND ?``. Listed in ``__table_args__`` so it
    # is part of the baseline Alembic schema. The legacy adoption runner
    # keeps the same name for databases created before Alembic.
    __table_args__ = (
        Index("ix_token_usage_emp_ts", "uid", "ts"),
    )

    # Read-only relationship for admin / debug views;
    # route code never traverses it to mutate the
    # contact.
    contact: Mapped["Contact"] = relationship(
        foreign_keys=[uid], viewonly=True
    )

    def __repr__(self) -> str:
        return (
            f"TokenUsage(id={self.id}, uid={self.uid}, "
            f"in={self.input_tokens}, out={self.output_tokens}, "
            f"ts={self.ts.isoformat()})"
        )
