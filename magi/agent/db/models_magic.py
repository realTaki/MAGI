"""ORM model ``magic`` — MAGIC, the MAGI Citizen rows.

Each row is a MAGIC runtime process bound to one :class:`MAGIS`.
``magis_id`` references :class:`MAGIS` (in
:mod:`magi.agent.db.models_magis`) and ``magic_position``
is one of ``"adam"`` (the manager, exactly one per MAGIS) /
``"eve"`` (a worker, N per MAGIS).

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

from sqlalchemy import (
    DateTime,
    ForeignKey,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column

from magi.agent.db.base import Base, utcnow_naive


if TYPE_CHECKING:
    from magi.agent.db.models_magis import MAGIS


class MAGIC(Base):
    """A MAGI runtime agent (a MAGI Citizen).

    Each ``MAGIC`` belongs to exactly one ``MAGIS`` via
    ``magis_id``. ``magic_position`` selects the archetype:
    ``"adam"`` (manager, exactly one per MAGIS) or
    ``"eve"`` (worker, N per MAGIS). ``provider`` /
    ``api_key`` carry the LLM credentials for the runtime
    process that binds to this row.
    """

    __tablename__ = "magic"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str | None] = mapped_column(
        String(100), nullable=True,
    )
    magis_id: Mapped[int] = mapped_column(
        ForeignKey("magis.id", ondelete="CASCADE"),
        nullable=False,
    )
    provider: Mapped[str | None] = mapped_column(
        String(64), nullable=True,
    )
    api_key: Mapped[str | None] = mapped_column(
        String(256), nullable=True,
    )
    magic_position: Mapped[str] = mapped_column(
        String(16), nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, onupdate=utcnow_naive, nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"MAGIC(id={self.id}, magis_id={self.magis_id}, "
            f"magic_position={self.magic_position!r})"
        )


def resolve_magic_credentials(
    position: str,
) -> tuple[str | None, str | None]:
    """Return ``(provider, api_key)`` from the first MAGIC
    row with ``magic_position == position``, or
    ``(None, None)`` when no matching MAGIC exists.

    This is the single read path for LLM credentials.
    Token-usage recording still writes to the
    ``token_usage`` table keyed by the Contact's ``uid``
    — the billing identity is the person, not the agent.

    Callers pass ``"adam"`` (WebUI chat) or ``"eve"``
    (TG bot / task runner). In v0 there is typically one
    MAGIC row per position; multi-magic dispatch is a
    future concern.
    """
    from sqlalchemy import select
    from magi.agent.db import open_session

    with open_session() as db:
        row = db.scalar(
            select(MAGIC).where(MAGIC.magic_position == position).limit(1)
        )
    if row is None:
        return None, None
    return row.provider, row.api_key
