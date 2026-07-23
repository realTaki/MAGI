"""ORM table for MAGI's mid-term memory.

What lives here:

  - **Important things** — facts the LLM should not forget
    (company policies, contract deadlines, "never do X"
    rules). High importance, long shelf life.
  - **Ongoing work** — projects, follow-ups, deadlines the
    LLM is mid-flight on. Set ``completed_at`` when done;
    the row stays in the table for the audit trail but
    drops out of the system-prompt block.

This is **MAGI's own mid-term memory** — the things the
operator has told the EVE to "remember". Person records
("Lily is in finance, telegram_id=9001") are **not** here;
they live in :mod:`magi.agent.memory.contacts` because
they describe a person, not a fact about the world.

The LLM writes to this table through the
``add_memory`` / ``update_memory`` / ``complete_memory`` /
``delete_memory`` tools (in :mod:`.tools`). It does NOT
mutate the table on every chat turn — only when the
operator explicitly says "记住 X" or the LLM judges the
fact worth persisting (operator policy, long-arc
context, future-deadline follow-ups).
"""

from __future__ import annotations

from datetime import datetime

from magi.agent.db.base import utcnow_naive

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from magi.agent.db.base import Base


# Memory kinds — what's stored. Person records are NOT
# here (they live in contacts); only facts and ongoing
# work.
KIND_IMPORTANT = "important"   # 长效事实 / 政策 / 合同
KIND_ONGOING = "ongoing"       # 正在进行的事

ALL_KINDS = frozenset({KIND_IMPORTANT, KIND_ONGOING})

# Sources — where the row came from. Mostly used for the
# dashboard ("was this operator-edited or auto-extracted
# from a chat?") and for the audit log.
SOURCE_MANUAL = "manual"   # Operator entered it via the WebUI.
SOURCE_EVE = "eve"         # EVE called ``add_memory`` itself.
SOURCE_SYSTEM = "system"   # Seeded by the platform (onboarding
                           # sets a couple of starting facts
                           # so a fresh EVE has context).


class MemoryEntry(Base):
    """One row of MAGI's mid-term memory.

    Indexed for the two access patterns the system
    actually uses:

      - "give me my important + ongoing memory
        (system prompt block)" — covers 90% of reads.
      - "give me the most-recent N rows for the
        ``list_memory`` tool" — the LLM's audit / load
        path.
    """

    __tablename__ = "memory_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    # The MAGI's own assigned employee. On a single-
    # instance setup this is the one assigned employee
    # for this node; could differ in a multi-tenant
    # future. ON DELETE CASCADE: removing the employee
    # clears their memory (the row is meaningless
    # without the owner).
    uid: Mapped[int] = mapped_column(
        ForeignKey("employees.id", ondelete="CASCADE"),
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
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
        DateTime, default=utcnow_naive, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, onupdate=utcnow_naive, nullable=False
    )

    __table_args__ = (
        # Primary read path: "give me my memory,
        # excluding completed, ordered by importance".
        Index(
            "ix_memory_entries_owner_importance",
            "uid", "completed_at", "importance",
        ),
    )

    def __repr__(self) -> str:
        flag = "✓" if self.completed_at else "·"
        return (
            f"MemoryEntry({flag} id={self.id} kind={self.kind!r} "
            f"subj={self.subject!r} imp={self.importance})"
        )