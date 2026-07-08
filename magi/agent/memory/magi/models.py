"""ORM table for MAGI's long-term memory.

What lives here:

  - **Important things** — facts the LLM should not forget
    (company policies, contract deadlines, "never do X"
    rules). High importance, long shelf life.
  - **Ongoing work** — projects, follow-ups, deadlines the
    LLM is mid-flight on. Set ``completed_at`` when done;
    the row stays in the table for the audit trail but
    drops out of the system-prompt block.
  - **People** — "Lily is in finance, telegram_id=9001,
    Q3 owner of the expense-approval queue". The
    ``person_employee_id`` FK points at the ``employees``
    row of the person being described; ``employee_id`` on
    this row is the *subject* (whose memory this lives
    in). On a single-MAGI deployment these usually
    coincide; in a multi-tenant future they could diverge.

Scope (``primary`` vs ``secondary``) is the freshness /
detail dial:

  - ``primary`` — the assigned employee on this MAGI.
    Dense: anything the LLM should reach for first
    (their tasks, their preferences, the people they
    work with). Goes into the system-prompt block.
  - ``secondary`` — other employees the assigned
    person bumps into. Sparse: "name + dept + role" is
    enough to recognise them when the LLM is asked to
    "send Lily a message". Not in the system-prompt
    block; the LLM ``load_memory`` tool fetches on
    demand.

The LLM writes to this table through the
``add_memory`` / ``update_memory`` / ``complete_memory`` /
``delete_memory`` tools
(:mod:`magi.agent.memory.tools`). It does NOT mutate the
table on every chat turn — only when the operator
explicitly says "remember that ..." or the LLM judges the
fact worth persisting (operator policy, long-arc
context, future-deadline follow-ups).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from magi.agent.db.base import Base


# Memory kinds — what's stored.
KIND_IMPORTANT = "important"   # 长效事实 / 政策 / 合同
KIND_ONGOING = "ongoing"       # 正在进行的事
KIND_PERSON = "person"         # 一个人的档案

ALL_KINDS = frozenset({KIND_IMPORTANT, KIND_ONGOING, KIND_PERSON})

# Memory scope — the "how much" dial.
SCOPE_PRIMARY = "primary"       # 主记忆：assigned employee 本人的事
SCOPE_SECONDARY = "secondary"   # 认识人的辅助记忆：directory 级别

ALL_SCOPES = frozenset({SCOPE_PRIMARY, SCOPE_SECONDARY})

# Sources — where the row came from. Mostly used for the
# dashboard ("was this operator-edited or auto-extracted
# from a chat?") and for the audit log.
SOURCE_MANUAL = "manual"   # Operator entered it via the WebUI.
SOURCE_EVE = "eve"         # EVE called ``add_memory`` itself.
SOURCE_SYSTEM = "system"   # Seeded by the platform (onboarding
                           # sets a couple of starting facts
                           # so a fresh EVE has context).


class MemoryEntry(Base):
    """One row of long-term memory.

    Indexed for the two access patterns the system
    actually uses:

      - "give me the primary memory for employee X
        (system prompt block)" — covers 90% of reads.
      - "give me the person record for employee Y" — the
        ``send_message`` tool's "find this person" path.
    """

    __tablename__ = "memory_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Whose memory this is. Almost always the MAGI's
    # ``assigned`` employee on a single-instance setup;
    # could differ in a multi-MAGI future.
    employee_id: Mapped[int] = mapped_column(
        ForeignKey("employees.id", ondelete="CASCADE"),
        nullable=False,
    )
    # The person BEING DESCRIBED, when ``kind=person``.
    # Self-FK to ``employees`` (the row may describe
    # another employee on the same MAGI). ``None`` for
    # ``important`` / ``ongoing`` kinds — those describe
    # facts / tasks, not people.
    #
    # ``ON DELETE SET NULL`` because the described
    # person may get hard-deleted (rare — soft delete
    # is the usual path); the memory row outlives the
    # person so the operator doesn't lose context.
    person_employee_id: Mapped[int | None] = mapped_column(
        ForeignKey("employees.id", ondelete="SET NULL"),
        nullable=True,
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    scope: Mapped[str] = mapped_column(
        String(16), nullable=False, default=SCOPE_PRIMARY
    )
    # Short human-readable title. Used as the bullet
    # in the system-prompt block. Bounded so a
    # runaway LLM can't dump 4 KB of text into a
    # single row's title.
    subject: Mapped[str] = mapped_column(String(200), nullable=False)
    # Full body. Markdown allowed. Capped at 8 KB
    # so a misbehaving tool call doesn't blow up the
    # prompt block.
    body: Mapped[str] = mapped_column(Text, nullable=False)
    # 1 (low) .. 5 (critical). ``important`` rows tend
    # to land at 3-5; ``ongoing`` rows are usually 2-3
    # so the operator can deprioritise a "nice to
    # remember" task. Used by the LLM when reading
    # memory back: high-importance rows get cited
    # first.
    importance: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    # Where the row came from. See ``SOURCE_*`` constants.
    source: Mapped[str] = mapped_column(
        String(16), nullable=False, default=SOURCE_EVE
    )
    # ``ongoing`` rows only. NULL = still in flight;
    # ISO UTC timestamp = the moment the operator
    # (or the EVE, on the operator's behalf) marked
    # it done. The system-prompt formatter filters
    # these out so completed work doesn't clutter
    # the LLM's working set.
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    __table_args__ = (
        # Primary read path: "give me employee X's
        # primary memory, ordered by importance desc".
        Index(
            "ix_memory_entries_owner_scope",
            "employee_id", "scope", "completed_at", "importance",
        ),
        # "Look up the memory row describing employee Y"
        # — the people-directory path.
        Index(
            "ix_memory_entries_person",
            "person_employee_id", "kind",
        ),
    )

    # Read-only relationships so the LLM / API can
    # render a person's name without a second query.
    owner: Mapped["Employee | None"] = relationship(
        foreign_keys=[employee_id], viewonly=True
    )
    person: Mapped["Employee | None"] = relationship(
        foreign_keys=[person_employee_id], viewonly=True
    )

    def __repr__(self) -> str:
        flag = "✓" if self.completed_at else "·"
        return (
            f"MemoryEntry({flag} id={self.id} kind={self.kind!r} "
            f"scope={self.scope!r} subj={self.subject!r} "
            f"imp={self.importance})"
        )


# Forward reference resolution — the relationship strings
# above resolve at mapper-config time, after both modules
# are imported. We import ``Employee`` here so the type
# checker sees it; SQLAlchemy only needs the string.
from magi.agent.db.models_employee import Employee  # noqa: E402