"""ORM table ``user_im_bindings`` — per-user IM channel bindings.

D.29 introduces this table as the single source of truth for
"which IM channel does this user own?". The dispatcher
(:mod:`magi.channels.dispatcher`) routes "send to user X via
channel Y" calls to the right channel adapter, and each adapter
reads its own rows out of this table.

Why a separate table (and not just extra columns on
``employees``):

  - Adding a new channel means adding a new adapter, not a new
    column. The schema doesn't need a migration per channel.
  - One user can be bound to multiple channels simultaneously
    (Telegram for fast ping + Email for nightly digest).
    A row-per-binding models that; columns don't.
  - The TG-specific binding legacy (Employee.telegram_id
    column) survives as a denormalised read-cache for the
    ``tg`` channel rows, kept in sync by the TG adapter's
    ``bind_im_id`` call. A future C8 hardening pass can drop
    the column entirely once all the legacy read sites move
    to the dispatcher.

Each row carries:

  - ``uid`` — the User's identity. FK to employees.id with
    ON DELETE CASCADE so deleting a User also drops their
    IM bindings.
  - ``channel`` — one of "telegram" / "slack" / "wechat" /
    ... (closed set; the dispatcher's adapter registry is the
    source of truth).
  - ``im_id`` — the per-channel IM identifier. The TG
    adapter stores the TG chat id (digit string); Slack
    stores a Slack mid; etc. Treated as opaque by domain
    code — only the channel adapter interprets it.

The composite UNIQUE on (uid, channel) is the row's primary
identity: one binding per (user, channel) pair. To rebind,
the adapter upserts.
"""

from __future__ import annotations

from sqlalchemy import (
    ForeignKey,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from magi.agent.db.base import Base


class UserImBinding(Base):
    """One IM-channel binding for one User.

    Domain code never reads ``im_id`` directly — that's the
    channel adapter's job. Domain code queries by ``uid`` +
    ``channel`` to discover "is this user bound?" or "what
    channels does this user have?".
    """

    __tablename__ = "user_im_bindings"

    # (uid, channel) is the row's primary identity — one
    # binding per (user, channel). To rebind, the adapter
    # upserts.
    #
    # FK to employees.id with CASCADE so removing a User
    # also drops their bindings (no orphan rows referencing
    # a non-existent User). This is also what the dispatcher
    # relies on when it deletes a User's session rows —
    # the IM binding goes too, so a future create() on the
    # same uid starts clean.
    uid: Mapped[int] = mapped_column(
        ForeignKey("employees.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )
    channel: Mapped[str] = mapped_column(String(32), primary_key=True, nullable=False)
    # Per-channel IM identifier. Stored as a string so
    # different channels (TG's digit-only mid, Slack's
    # alphanumeric ws id, etc.) all fit without per-channel
    # type specialisation. Channel adapter parses it.
    im_id: Mapped[str] = mapped_column(String(128), nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "uid", "channel",
            name="ux_user_im_bindings_uid_channel",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"UserImBinding(uid={self.uid}, "
            f"channel={self.channel!r}, im_id=…)"
        )
