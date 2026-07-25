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

  - ``"admin"``    — operator, can sign in to the WebUI.
  - ``"assigned"`` — the person this MAGI serves.
  - ``"contact"``  — org member, not directly served.
  - ``"guest"``    — external / unknown.

``telegram_id`` is the bound TG chat id (NULL until the
/start binding flow). Unique across non-NULL values.

``separated_at`` is the soft-delete flag — NULL means
active, a timestamp means the contact was marked as
separated (formerly "已离职").

``provider`` / ``api_key`` carry the LLM credentials.
``api_key`` is a secret — never returned in plain text by
any endpoint. The F1 follow-up will move these to
``Magi``; for now they stay on the contact row for v0
compatibility.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from magi.agent.db.base import Base, utcnow_naive


# Sources — mirrors :mod:`magi.agent.memory.magi.models`.
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
    #   "admin"    — operator (can sign into WebUI).
    #   "assigned" — served by this MAGI.
    #   "contact"  — org member, not directly served.
    #   "guest"    — external / unknown.
    role: Mapped[str] = mapped_column(
        String(16), nullable=False, default="contact"
    )

    # LLM provider credentials. F1 follow-up moves these
    # to ``Magi``; for v0 they live on the contact row.
    provider: Mapped[str | None] = mapped_column(String(32))
    api_key: Mapped[str | None] = mapped_column(String(512))

    # Bound TG chat id. Unique across non-NULL values
    # (enforced by the unique index in migrations).
    telegram_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # Soft-delete. NULL = active.
    separated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # From contact_entries — free-form markdown about this
    # person. LLM-managed via add_contact/update_contact tools.
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
            f"role={self.role!r})"
        )
