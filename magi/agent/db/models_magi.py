"""ORM table ``magis`` — the MAGI agent rows.

Each row is a MAGI runtime process bound to one :class:`MAGIC`.
``magic_id`` references :class:`MAGIC` (in
:mod:`magi.agent.db.models_magic`) and ``magic_position``
is one of ``"adam"`` (the manager, exactly one per MAGIC) /
``"eve"`` (a worker, N per MAGIC).

The provider / api_key columns carry the LLM provider and key
for the MAGI runtime that boots for this row. They are
read by :func:`magi.agent.loop.handle_message` on each call.

Forward references to ``MAGIC`` resolve at mapper-config time
via the standard ``TYPE_CHECKING`` + ``from __future__ import
annotations`` pattern.
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
    from magi.agent.db.models_magic import MAGIC


class Magi(Base):
    """A MAGI runtime agent.

    Each ``Magi`` belongs to exactly one ``MAGIC`` via
    ``magic_id``. ``magic_position`` selects the archetype:
    ``"adam"`` (manager, exactly one per MAGIC) or
    ``"eve"`` (worker, N per MAGIC). ``provider`` /
    ``api_key`` carry the LLM credentials for the runtime
    process that binds to this row.
    """

    __tablename__ = "magis"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str | None] = mapped_column(
        String(100), nullable=True,
    )
    magic_id: Mapped[int] = mapped_column(
        ForeignKey("magics.id", ondelete="CASCADE"),
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
            f"Magi(id={self.id}, magic_id={self.magic_id}, "
            f"magic_position={self.magic_position!r})"
        )