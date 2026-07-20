"""ORM table ``employees`` — one row per person in the company.

Schema is intentionally minimal — the C1.1 baseline has
the dept assignment + LLM provider config (each employee
can route to a different model when their EVE handles
traffic). C1.2 grows the lifecycle (email, status, quiet
hours); C2 adds the telegram_id binding.

The cross-table relationships (department, led_department)
point at :class:`Department` (in
:mod:`magi.agent.db.models_department`). FK columns use
string literals so this file has no runtime dependency
on the Department module; the ``relationship(back_populates=...)``
strings are resolved when SQLAlchemy configures the
mapper, after both modules are imported.
"""

from __future__ import annotations

from datetime import datetime

from magi.agent.db.base import utcnow_naive
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from magi.agent.db.base import Base


if TYPE_CHECKING:
    # Type-only import for the relationship back-refs.
    # No runtime dependency — keeps the module loading
    # order trivial (FK strings resolve at mapper
    # configuration time, after both modules import).
    from magi.agent.db.models_department import Department


class Employee(Base):
    """A person in the company.

    ``api_key`` is the employee's LLM-provider key. It is
    treated as a secret — never returned in plain text by
    any endpoint, only as a boolean ``api_key_set`` flag +
    a ``last4`` suffix so the UI can show ``"sk-…abcd"``
    without leaking the full value. Stored plain-text for
    C0; the C8 hardening pass encrypts at rest with a
    deployer-supplied ``MAGI_SECRET``.
    """

    __tablename__ = "employees"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(120))
    # Direct FK to the dept the employee belongs to. Nullable:
    # an employee can exist without a department assignment
    # yet (the UI exposes a "未指定部门" pseudo-section that
    # filters on this being NULL).
    department_id: Mapped[int | None] = mapped_column(
        ForeignKey("departments.id", ondelete="SET NULL"),
        nullable=True,
    )
    # LLM provider. For C1.1 this is a free-text string; C3
    # adds routing logic (Anthropic / OpenAI / Ollama / etc.).
    provider: Mapped[str | None] = mapped_column(String(32))
    # Secret. Never logged, never returned in plain text.
    api_key: Mapped[str | None] = mapped_column(String(512))
    # Soft-delete flag. NULL means active; non-NULL is the
    # timestamp at which the employee was marked separated.
    # Separated employees are hidden by default in department
    # views (toggleable) and exposed via the dedicated
    # "已离职员工" scope — the dashboard never hard-deletes
    # employees because the org needs the historical record
    # (manager_of, past assignments, audit references).
    separated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Role on the MAGI instance — four-value enum:
    #   - "admin"    : operator / deployer. Can sign in to
    #                  Adam's WebUI. Sees the audit log, the
    #                  employee directory, the channel admin.
    #                  The TG bot logs admin messages but
    #                  doesn't run them through the LLM (we
    #                  don't burn the operator's API key on
    #                  chitchat).
    #   - "assigned" : the person this MAGI serves. Their
    #                  TG messages go through the agent loop.
    #                  In v0 single-instance, all "real"
    #                  employees default to ``assigned``.
    #   - "employee" : another company employee. NOT
    #                  served by this MAGI. Cross-MAGI
    #                  access (an employee of company X
    #                  talking to company Y's MAGI) is a
    #                  future concern; for v0 the bot
    #                  politely refuses their messages.
    #   - "guest"    : not in this company at all — a
    #                  visitor. The bot's first-touch
    #                  discovery reply is the path that
    #                  handles this; a row with role='guest'
    #                  is rare and usually operator-created.
    # v0 writes ``admin`` (onboarding) or ``assigned``
    # (dashboard create). ``employee`` and ``guest`` exist
    # so future C6+ (cross-MAGI access, public visitors)
    # can read them without a schema change.
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="assigned")
    # Telegram chat id of the bound user, when known. NULL
    # for employees who haven't completed the /start binding
    # flow (C2). Unique across the table — one chat_id
    # binds to at most one employee. Stored on the row so
    # the TG bot can resolve a chat_id to its employee in
    # a single ORM read; the older ``meta``-key mapping
    # (telegram.user.<chat_id>.employee_id) is deprecated
    # but kept in the codebase for back-compat with
    # any state that hasn't been migrated yet.
    # Uniqueness is enforced via the ``ux_employees_telegram_id``
    # index created in
    # :data:`magi.agent.db.migrations._UNIQUE_INDEX_MIGRATIONS`
    # — a plain ``UNIQUE`` constraint here would block ALTER
    # TABLE on a pre-existing table (SQLite refuses "Cannot
    # add a UNIQUE column"). The index is ``WHERE telegram_id
    # IS NOT NULL`` so multiple NULLs (un-bound employees)
    # don't collide, matching the spirit of SQL UNIQUE.
    telegram_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, onupdate=utcnow_naive, nullable=False
    )

    # The department this employee belongs to (not to be
    # confused with ``Department.manager`` which is the
    # "lead" pointer). Single FK column + backref from
    # Department.employees so the dashboard can ask
    # "who is in this dept" without a second query.
    department: Mapped["Department | None"] = relationship(
        back_populates="employees",
        foreign_keys=[department_id],
    )

    # The department this employee leads, if any.
    # ``remote_side`` disambiguates the two FKs
    # (Department.manager and Department.manager_id) on
    # the parent side.
    led_department: Mapped["Department | None"] = relationship(
        back_populates="manager",
        foreign_keys="Department.manager_id",
    )

    def __repr__(self) -> str:
        return f"Employee(id={self.id}, name={self.name!r}, department_id={self.department_id})"