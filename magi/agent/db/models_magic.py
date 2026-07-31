"""ORM model ``magic`` — MAGIC, the MAGI Citizen rows.

Each row is one independently created MAGI.  Membership in a
:class:`MAGIS` and the role held there are represented by
``MAGISMembership`` rows, rather than fixed columns on the MAGI itself.

The provider / api_key columns carry the LLM provider and key
for the MAGIC runtime. They are the **single source of truth**
for LLM credentials — the ``contacts`` table does NOT hold
provider/api_key (removed in the D.30 credential refactor).
Read via :func:`resolve_magic_credentials`.

Forward references to ``MAGIS`` resolve at mapper-config time
via the standard ``TYPE_CHECKING`` + ``from __future__ import
annotations`` pattern.

Naming convention
----------------

After the 2026-07 naming refresh, this class is named
``MAGIC`` (one row = one individual MAGI agent). The Python
class ``MAGIC`` represents an individual; the Python class
``MAGIS`` (in :mod:`magi.agent.db.models_magis`) represents
a group of MAGI Citizens. ``__tablename__ = "magic"``.
"""

from __future__ import annotations

from datetime import datetime

from typing import TYPE_CHECKING

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from magi.agent.db.base import Base, utcnow_naive


if TYPE_CHECKING:
    from magi.agent.db.models_magis import MAGIS


class MAGIC(Base):
    """A MAGI runtime agent (a MAGI Citizen).

    A MAGI is created independently.  Its memberships determine the
    MAGIS instructions and role instructions it receives.  ``provider`` /
    ``api_key`` carry the LLM credentials for the runtime process that
    binds to this row.
    """

    __tablename__ = "magic"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str | None] = mapped_column(
        String(100), nullable=True,
    )
    provider: Mapped[str | None] = mapped_column(
        String(64), nullable=True,
    )
    api_key: Mapped[str | None] = mapped_column(
        String(256), nullable=True,
    )
    instruction: Mapped[str] = mapped_column(default="", nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, onupdate=utcnow_naive, nullable=False
    )

    def __repr__(self) -> str:
        return f"MAGIC(id={self.id}, name={self.name!r})"


def resolve_magic_credentials(magic_id: int | None = None) -> tuple[str | None, str | None]:
    """Return credentials for the runtime MAGI.

    This is the single read path for LLM credentials.
    Token-usage recording still writes to the
    ``token_usage`` table keyed by the Contact's ``uid``
    — the billing identity is the person, not the agent.

    When ``magic_id`` is omitted, resolve the root MAGIS's Adam.  This
    keeps root-runtime callers simple while avoiding a global role lookup.
    """
    from sqlalchemy import select
    from magi.agent.db import open_session

    with open_session() as db:
        if magic_id is not None:
            row = db.get(MAGIC, magic_id)
        else:
            from magi.agent.db.models_magis import MAGIS
            root = db.scalar(select(MAGIS).where(MAGIS.parent_id.is_(None)).order_by(MAGIS.id))
            row = db.get(MAGIC, root.adam_id) if root and root.adam_id else None
    if row is None:
        return None, None
    return row.provider, row.api_key
