"""ORM table ``auth_credentials`` — per-UID login credentials.

Stores the secret material the runtime needs to authenticate
a :class:`magi.bus.models.local.contact.Contact` row when the
user signs in via WebUI. Today there is one ``kind`` value:

  - ``"password"`` — the user has a password they can type
    into the WebUI login page. The hash is ``scrypt`` with
    a per-row random salt.

Why a separate table
--------------------

The :class:`Contact` row is the *business* identity — name,
role, notes, IM bindings. Login credentials are *security*
state and have a different lifecycle:

  - a credential can be reset (re-hashed) without touching
    the contact's profile;
  - a credential can be revoked (deleted) without removing
    the contact;
  - new credential kinds land in this table without
    reshaping :class:`Contact` — yet a future email / OAuth
    path can reuse the same schema.

The runtime-side contact ↔ credential relationship is 1:N:
a user can have both a password and a TG code (today;
email tomorrow). The unique key on ``(uid, kind)`` keeps
that 1:N clean.

Storage format
--------------

``secret_hash`` is a UTF-8 string of the form
``"scrypt$N$r$p$<salt_b64>$<hash_b64>"``. We keep the
parameters in the hash so a future tuning (``n=2**15``)
re-hashes on first verify rather than forcing a migration.
See :mod:`magi.webui.api.password_utils` for the verify
helper.

A row's existence is the "is this method enabled?" signal.
Deleting the row revokes the credential — there is no
``enabled`` column to flip, by design (we never want a
"disabled but still verifyable" row to leak).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from magi.db.base import Base, utcnow_naive


# Allowed kinds. ``tg_code`` is a placeholder for the future
# "the user opted in to TG code delivery" row — currently
# the login code lives in the legacy ``settings`` KV at
# ``auth.login_code.<uid>`` and is not mirrored here. Once
# the migration off the KV is done, that line leaves the
# enum. Today only ``password`` is written.
PASSWORD = "password"
TG_CODE = "tg_code"
_VALID_KINDS: tuple[str, ...] = (PASSWORD, TG_CODE)


class AuthCredential(Base):
    """One login credential for one contact.

    The ``uid`` is a foreign key to ``contacts.id`` but
    lacks the CASCADE — operators don't casually delete
    contacts (the chat history hinges on them), and
    credentials are dropped separately as part of the
    "revoke this login method" path. The intent is
    "removing a credential is a security event, not a
    cleanup detail".
    """

    __tablename__ = "auth_credentials"

    id: Mapped[int] = mapped_column(primary_key=True)
    uid: Mapped[int] = mapped_column(
        ForeignKey("contacts.id"), nullable=False,
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    secret_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    # ``created_at`` is when the credential was hashed for
    # the first time. ``updated_at`` is the last rehash
    # (when the user changed their password). The two are
    # surfaced to the WebUI Settings → Security card so
    # the operator can see how old the credential is.
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, onupdate=utcnow_naive, nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("uid", "kind", name="ux_auth_credentials_uid_kind"),
        Index("ix_auth_credentials_uid", "uid"),
    )

    def __repr__(self) -> str:
        return (
            f"AuthCredential(id={self.id}, uid={self.uid}, "
            f"kind={self.kind!r})"
        )


__all__ = ["AuthCredential", "PASSWORD", "TG_CODE"]
