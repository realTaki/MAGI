"""ORM table ``magics`` — a tree of MAGI teams (councils).

A :class:`MAGIC` row is the org container: one team of
MAGI agents (``magis`` table, see :mod:`.models_magi`)
coordinated by exactly one ``Magi`` with ``magic_position
= 'adam'``. The tree shape is maintained via
``parent_id`` self-FK.

``adam_id`` references ``magis.id`` (the manager MAGI for
this team) and is nullable — a ``MAGIC`` can exist before
its adam MAGI row is provisioned.

The cross-table relationships (``adam``, ``children``,
``parent``) resolve at mapper-config time via the standard
``TYPE_CHECKING`` + ``from __future__ import annotations``
pattern — same as :mod:`.models_employee`.
"""

from __future__ import annotations

from datetime import datetime

from typing import TYPE_CHECKING

from sqlalchemy import (
    DateTime,
    ForeignKey,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from magi.agent.db.base import Base, utcnow_naive


if TYPE_CHECKING:
    from magi.agent.db.models_magi import Magi


class MAGIC(Base):
    """A MAGI team (council).

    ``adam_id`` references :class:`Magi` (the manager
    MAGI for this team) and is nullable — a ``MAGIC`` can
    exist before its adam MAGI row is provisioned.
    """

    __tablename__ = "magics"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("magics.id", ondelete="RESTRICT"),
        nullable=True,
    )
    adam_id: Mapped[int | None] = mapped_column(
        ForeignKey("magis.id", ondelete="SET NULL"),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, onupdate=utcnow_naive, nullable=False
    )

    # Self-referential tree. ``remote_side=id`` is the magic
    # that tells SQLAlchemy which side of the parent_id FK
    # is the "many" side, so ``children`` is a list of
    # MAGI teams rather than a back to the parent.
    children: Mapped[list["MAGIC"]] = relationship(
        back_populates="parent",
        cascade="all, delete-orphan",
        single_parent=True,
    )
    parent: Mapped["MAGIC | None"] = relationship(
        back_populates="children",
        remote_side="MAGIC.id",
    )

    adam: Mapped["Magi | None"] = relationship(
        foreign_keys=[adam_id],
    )

    def __repr__(self) -> str:
        return f"MAGIC(id={self.id}, name={self.name!r}, parent_id={self.parent_id})"