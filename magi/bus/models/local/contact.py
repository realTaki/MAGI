
"""ORM table ``contacts`` — every person MAGI knows.

This is the **unified** contact table that replaces three
pre-refactor tables:

  - ``contacts`` — was the user directory (name, role,
    provider, api_key, telegram_id, separated_at).
  - ``contact_entries`` — was the per-MAGI contact
    directory (owner_id, person_id FK pairs + notes +
    source + last_seen_at). Now folded into ``contacts``:
    the ``notes`` field is free-form markdown about the
    person, and ``source`` tracks who recorded it.
  - ``user_im_bindings`` — was per-user IM channel
    bindings ((uid, channel, im_id) rows). Now the
    ``telegram_id`` lives directly on ``contacts``;
    future channels (WeChat, Slack, …) will add their
    own columns as needed.

Schema is deliberately flat — a ``Contact`` row **is** the
person. The old ``contact_entries.owner_id``/``person_id``
split is gone: there is one row per person, and MAGI's
knowledge about them lives in ``notes``.

``role`` is the service role relative to MAGI:

  - ``"assigned"`` — the person this MAGI serves. Also
    the typical operator in a single-MAGI install
    (``role='assigned', admin=True``).
  - ``"guest"``    — external / unknown. The default
    for ``POST /api/contacts`` with no role specified.

There is no longer a ``"contact"`` role — it was removed
after a long period where it was functionally identical to
``"guest"`` (every gate that refused ``contact`` also
refused ``guest``, with no operator-only distinction
between the two). The dev-mode collapsed baseline
(``0001_baseline``) ships the final enum directly, so
existing dev DBs are aligned on first ``init_orm``.

WebUI sign-in rights are carried by the separate
``admin`` boolean (``True`` ↔ can sign into the operator
console). The two fields are intentionally independent —
a person can be ``role='assigned', admin=False`` (the
served user who never logs in) or ``role='assigned',
admin=True`` (the served user who also drives the
console).

``telegram_id`` is the bound TG chat id (NULL until the
/start binding flow). Unique across non-NULL values.

``separated_at`` is the soft-delete flag — NULL means
active, a timestamp means the contact was marked as
separated (formerly "已离职").

LLM credentials live on the ``magis`` table
(:func:`magi.agent.llm.factory.get_provider` resolves them
at runtime), not on ``contacts``. Token usage is still
recorded per-Contact via ``token_usage.uid``.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from magi.bus.db.base import Base, utcnow_naive


# Sources — mirrors :mod:`magi.agent.memory.self.models`.
SOURCE_MANUAL = "manual"
SOURCE_EVE = "eve"
SOURCE_SYSTEM = "system"


class Contact(Base):
    """A person MAGI knows. One row per person.

    Replaces ``Contact``, ``ContactEntry``, and
    ``UserImBinding`` in the unified schema.
    """

    __tablename__ = "contacts"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(120))

    # Service role — how this person relates to MAGI.
    #   "assigned" — the person this MAGI serves. The
    #               typical operator in a single-MAGI
    #               install is ``role='assigned', admin=True``.
    #   "guest"    — external / unknown. The default for
    #               ``POST /api/contacts`` with no role
    #               specified.
    #
    # WebUI sign-in rights are NOT encoded here — see the
    # ``admin`` boolean below. The two fields are
    # intentionally independent.
    #
    # The legacy ``"contact"`` value was functionally
    # identical to ``"guest"`` (every gate refused both)
    # and was removed in the 2024 role/admin split. The
    # dev-mode collapsed ``0001_baseline`` ships the final
    # enum directly so existing dev DBs land cleanly.
    role: Mapped[str] = mapped_column(
        String(16), nullable=False, default="guest"
    )

    # WebUI sign-in rights. ``True`` if this contact can
    # authenticate to the operator console
    # (``/api/auth/me`` accepts the session cookie). The
    # tasks creator-role gate also checks this — an admin
    # can create scheduled tasks regardless of role,
    # mirroring the pre-split semantic where "admin"
    # meant "operator who could drive the system".
    #
    # Independent of ``role`` on purpose: an assigned user
    # can also be their own operator (``admin=True``), and
    # a backend operator doesn't have to be the person
    # being served (``role='contact', admin=True``).
    admin: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0",
    )

    # Bound TG chat id. Unique across non-NULL values
    # (enforced by the unique index in migrations).
    telegram_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # Soft-delete. NULL = active.
    separated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Deprecated — use ``contact_notes`` table instead.
    # Kept for backward compat.  Tools write to
    # ``contact_notes``, not here.
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # Where the notes came from.
    source: Mapped[str] = mapped_column(
        String(16), nullable=False, default=SOURCE_MANUAL
    )

    # Last time MAGI recorded something about this person.
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, onupdate=utcnow_naive, nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"Contact(id={self.id}, name={self.name!r}, "
            f"role={self.role!r}, admin={self.admin})"
        )


class ContactNote(Base):
    """One fact/memory about a contact.

    Two kinds live in this table:

    - ``kind='permanent'`` — the long-arc fact stream the
      LLM writes via ``add_contact_note`` (e.g. "Lily 在财务部",
      "Mark prefer Slack"). Each call creates one row.
    - ``kind='daily'`` — the short-arc daily log the LLM
      writes via ``update_daily_note``. One row per
      ``(contact_id, note_date)``; the tool appends to the
      existing body. ``note_date`` is naive UTC midnight of
      the day the row belongs to.

    The old single ``contacts.notes`` text column is
    deprecated; the ``format_contact_block`` /
    ``format_daily_note_block`` formatters aggregate from here.
    """

    __tablename__ = "contact_notes"

    id: Mapped[int] = mapped_column(primary_key=True)
    contact_id: Mapped[int] = mapped_column(
        ForeignKey("contacts.id", ondelete="CASCADE"),
        nullable=False,
    )
    note: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(
        String(16), nullable=False, default=SOURCE_EVE,
    )
    # Memory kind. Permanent rows are individual facts; daily
    # rows are the running log the agent loop appends to.
    kind: Mapped[str] = mapped_column(
        String(16), nullable=False, default="permanent",
    )
    # Naive UTC midnight of the day this row belongs to.
    # NULL on permanent rows; non-null on daily rows so the
    # morning/night report can do ``WHERE kind='daily' AND
    # note_date=date('now')``. The local-timezone-aware
    # "what day is it for the operator" question is solved
    # at the *query* layer — the row key stays UTC for
    # deterministic date arithmetic.
    note_date: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, onupdate=utcnow_naive, nullable=False,
    )

    def __repr__(self) -> str:
        preview = self.note[:40].replace("\n", " ") if self.note else ""
        return (
            f"ContactNote(id={self.id}, contact_id={self.contact_id}, "
            f"kind={self.kind!r}, note={preview!r}...)"
        )
