"""ORM table for the MAGI's contact directory.

A single table that records what the MAGI knows about
each person (employee). One row per (owner, person)
pair — the LLM's ``add_contact`` tool updates the
existing row if called twice for the same person.

Design choices:

  - **No scope field.** Decided against primary /
    secondary because "is this the current chatter?"
    is the only thing the system prompt actually
    branches on, and the per-chat formatter in
    :mod:`.prompt` does that branch by FK lookup at
    render time. Storing scope in the row would just
    duplicate state.
  - **No kind / subject split.** A contact is just
    free-form markdown about a person. The
    schema is deliberately narrow: ``role`` is the
    only structured field (``employee.telegram_id``
    already gives us the rest), ``notes`` is the LLM-
    managed free-form body.
  - **``last_seen_at``** is updated whenever the LLM
    calls ``add_contact`` for this person again, so
    "remember the last time we talked about Lily" is a
    trivial SELECT order-by.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from magi.agent.db.base import Base


# Sources — mirrors :mod:`magi.agent.memory.magi.models`
# (the two packages share a vocabulary).
SOURCE_MANUAL = "manual"
SOURCE_EVE = "eve"
SOURCE_SYSTEM = "system"


class ContactEntry(Base):
    """One row of the contact directory.

    ``(owner_id, person_id)`` is unique — there is one
    record per person per MAGI.
    """

    __tablename__ = "contact_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    # The MAGI that owns this record. On a single-
    # instance setup this is the assigned employee
    # for this node. CASCADE on delete: removing
    # the MAGI's owner employee clears the
    # directory.
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("employees.id", ondelete="CASCADE"),
        nullable=False,
    )
    # The person being described. NOT NULL — every
    # contact row must point at an existing employee
    # (the table's whole point is to attach facts to
    # people). SET NULL on delete: if the person
    # leaves the company, the row becomes an orphan
    # rather than disappearing (operator keeps the
    # history; the dashboard hides it).
    person_id: Mapped[int] = mapped_column(
        ForeignKey("employees.id", ondelete="SET NULL"),
        nullable=True,
    )
    # The person's role at the company as of this
    # contact record's creation. The role on the
    # underlying Employee row can change; the
    # contact's snapshot stays. Useful for
    # "Lily was finance lead in Q3" queries.
    role: Mapped[str | None] = mapped_column(String(64))
    # Free-form markdown. LLM-managed; this is where
    # "Lily is in finance, owns expense approvals,
    # prefers Slack over email" lives. Capped at
    # 8 KB so a runaway tool call doesn't blow the
    # prompt block.
    notes: Mapped[str] = mapped_column(Text, nullable=False)
    # Where the row came from.
    source: Mapped[str] = mapped_column(
        String(16), nullable=False, default=SOURCE_EVE
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    __table_args__ = (
        # One row per (owner, person) pair.
        UniqueConstraint(
            "owner_id", "person_id",
            name="uq_contact_entries_owner_person",
        ),
        # Primary read path: "give me my contacts,
        # ordered by last seen".
        Index(
            "ix_contact_entries_owner_last_seen",
            "owner_id", "last_seen_at",
        ),
    )

    # Read-only back-refs for the LLM-facing JSON
    # rendering. Never mutate via these.
    owner: Mapped["Employee | None"] = relationship(
        foreign_keys=[owner_id], viewonly=True
    )
    person: Mapped["Employee | None"] = relationship(
        foreign_keys=[person_id], viewonly=True
    )

    def __repr__(self) -> str:
        return (
            f"ContactEntry(id={self.id}, owner_id={self.owner_id}, "
            f"person_id={self.person_id}, role={self.role!r})"
        )


# Type-only import so the relationship strings resolve
# at mapper-config time without a runtime cycle.
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from magi.agent.db.models_employee import Employee