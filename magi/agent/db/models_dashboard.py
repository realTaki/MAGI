"""Dashboard-domain ORM tables — ``action_items`` + ``token_usage``.

Two read-mostly tables that power the operator's
dashboard:

  - :class:`ActionItem` — a to-do surface; the dashboard
    surfaces "set your LLM credentials" / future EVE-driven
    follow-ups here. ``kind`` is a free-form string so C4
    can add new categories without a migration.
  - :class:`TokenUsage` — one row per outbound LLM call;
    powers the per-employee token-bill aggregation
    endpoint. Permanent: this table grows indefinitely so
    week/month aggregates stay accurate.

Both reference :class:`magi.agent.db.models_org.Employee`
for the cross-table employee FK. No inter-table
relationship between ActionItem and TokenUsage — they
share a domain (the dashboard) but not a query path.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from magi.agent.db.base import Base


class ActionItem(Base):
    """A to-do surfaced to an admin in the dashboard.

    Created by system paths (``save_admin`` etc.) and, from
    C4, by EVEs that want to nudge the operator about a
    follow-up. Dismissed / completed by the operator via
    ``POST /api/action_items/{id}/complete`` — auto-completion
    is deliberately out of scope (the operator may want to
    dismiss a row for reasons unrelated to the underlying
    state).
    """

    __tablename__ = "action_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    # See class docstring on the SET NULL choice. Nullable
    # because the FK target can disappear (admin removed),
    # leaving the action item as an orphan until something
    # re-binds it.
    employee_id: Mapped[int | None] = mapped_column(
        ForeignKey("employees.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Stable identifier per row category. Free-form string;
    # the schema doesn't enforce a closed enum so C4 can add
    # new kinds without a migration. Picked to be short +
    # readable in /api/action_items?kind=<here> URLs.
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(String(1000))
    # Where the "去设置" button navigates. Currently a path
    # relative to the dashboard; future in-app tab switches
    # can replace it with a deep-link state.
    target_url: Mapped[str | None] = mapped_column(String(500))
    # "normal" (default) or "high" — C4 uses "high" for
    # time-sensitive follow-ups. Not a closed enum here for
    # the same reason as ``kind``.
    priority: Mapped[str] = mapped_column(
        String(16), nullable=False, default="normal"
    )
    # Who created the row. "system" for save_admin /
    # similar, "eve" when C4 EVE-driven rows land, "user"
    # for future operator-authored reminders. Useful for
    # grouping + filtering later.
    source: Mapped[str] = mapped_column(
        String(16), nullable=False, default="system"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    # Null = open. The "I clicked 完成" stamp.
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )
    completed_by_employee_id: Mapped[int | None] = mapped_column(
        ForeignKey("employees.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Optional reason captured at complete-time; useful for
    # C4 EVE-driven rows where the operator may say "I'll do
    # it next week" (the EVE then reads it back).
    completion_note: Mapped[str | None] = mapped_column(String(500))
    # Hidden without recording "I did it". Distinct from
    # completed_at IS NOT NULL — both remove from the open
    # list, but a dismissed row never claims the underlying
    # action was performed. Used by the future "hide this
    # prompt" affordance; v0 leaves dismissed at False.
    dismissed: Mapped[bool] = mapped_column(default=False, nullable=False)

    # Viewonly relationships so route code never mutates
    # Employee via the ActionItem collection.
    employee: Mapped["Employee | None"] = relationship(
        foreign_keys=[employee_id], viewonly=True
    )
    completed_by: Mapped["Employee | None"] = relationship(
        foreign_keys=[completed_by_employee_id], viewonly=True
    )

    def __repr__(self) -> str:
        return (
            f"ActionItem(id={self.id}, kind={self.kind!r}, "
            f"employee_id={self.employee_id}, "
            f"completed={self.completed_at is not None}, "
            f"dismissed={self.dismissed})"
        )


class TokenUsage(Base):
    """One row per outbound LLM call.

    Powers the per-employee token-bill aggregation endpoint
    (see ``magi.channels.webui.api.employee_metrics``).
    Permanent: this table is meant to grow indefinitely
    so week/month aggregates stay accurate. The operator-
    facing endpoint ``/api/employees/{id}/token-usage``
    answers the "what did this employee cost?" question;
    the session JSON files (D.6) answer the "what was
    said?" question. v0 doesn't carry a separate audit
    log — those two surfaces cover the same questions
    the audit view would.

    The four token fields follow the Anthropic SDK's
    ``Usage`` shape so the helper in ``agent.py`` can
    copy keys verbatim. The v0 UI only renders
    ``input_tokens`` + ``output_tokens``; cache fields are
    stored so a future dashboard view doesn't need a schema
    change to expose them.

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
    """
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
        DateTime, default=datetime.utcnow, nullable=False
    )

    # Composite index — supports the aggregation endpoint's
    # ``WHERE employee_id = ? AND ts BETWEEN ? AND ?``.
    # Listed in ``__table_args__`` so it gets created
    # alongside the table by ``create_all`` on a fresh DB;
    # for an existing DB, ``_INDEX_MIGRATIONS`` below
    # patches it in via ``CREATE INDEX IF NOT EXISTS``.
    __table_args__ = (
        Index("ix_token_usage_emp_ts", "employee_id", "ts"),
    )

    # Read-only relationship for admin / debug views; route
    # code never traverses it to mutate the employee.
    employee: Mapped["Employee"] = relationship(
        foreign_keys=[employee_id], viewonly=True
    )

    def __repr__(self) -> str:
        return (
            f"TokenUsage(id={self.id}, employee_id={self.employee_id}, "
            f"in={self.input_tokens}, out={self.output_tokens}, "
            f"ts={self.ts.isoformat()})"
        )


# Forward reference for the relationship strings above; resolved
# at import time when :mod:`magi.agent.db` is loaded.
from magi.agent.db.models_org import Employee  # noqa: E402
