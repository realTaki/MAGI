"""ORM model for ``magis`` — MAGIS, the MAGI Society tree.

A :class:`MAGIS` row is the org container: one group of
MAGI agents (``magic`` table, see :mod:`.models_magic`)
coordinated by exactly one ``MAGIC`` with ``magic_position
= 'adam'``. The tree shape is maintained via
``parent_id`` self-FK.

``adam_id`` references ``magic.id`` (the manager MAGIC for
this MAGIS) and is nullable — a ``MAGIS`` can exist before
its adam MAGIC row is provisioned.

The cross-table relationships (``adam``, ``children``,
``parent``) resolve at mapper-config time via the standard
``TYPE_CHECKING`` + ``from __future__ import annotations``
pattern — same as :mod:`.models_contact`.

Naming convention
----------------

After the 2026-07 naming refresh:

  - A **MAGIS** is a MAGI Society: a group of MAGI Citizens.
    Its row lives in the ``magis`` table.
  - A **MAGIC** is an individual MAGI Citizen. Its row lives
    in the ``magic`` table.
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
    from magi.agent.db.models_magic import MAGIC


class MAGIS(Base):
    """A MAGI Society (a group of MAGIs).

    ``adam_id`` references :class:`MAGIC` (the manager
    MAGIC for this Society) and is nullable — a ``MAGIS``
    can exist before its adam MAGIC row is provisioned.
    """

    __tablename__ = "magis"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("magis.id", ondelete="RESTRICT"),
        nullable=True,
    )
    adam_id: Mapped[int | None] = mapped_column(
        ForeignKey("magic.id", ondelete="SET NULL"),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, onupdate=utcnow_naive, nullable=False
    )

    # Self-referential tree. ``remote_side=id`` is the marker
    # that tells SQLAlchemy which side of the parent_id FK
    # is the "many" side, so ``children`` is a list of
    # child MAGISes rather than a back to the parent.
    #
    # ``cascade="all, delete-orphan"`` would silently nuke
    # child MAGIS rows on parent delete (overriding the
    # RESTRICT FK guard and the API's reparent step). The
    # delete endpoint re-parents children explicitly before
    # issuing ``session.delete(parent)``; we therefore *do
    # not* want ORM-side cascading here.
    children: Mapped[list["MAGIS"]] = relationship(
        back_populates="parent",
    )
    parent: Mapped["MAGIS | None"] = relationship(
        back_populates="children",
        remote_side="MAGIS.id",
    )

    adam: Mapped["MAGIC | None"] = relationship(
        foreign_keys=[adam_id],
    )

    def __repr__(self) -> str:
        return f"MAGIS(id={self.id}, name={self.name!r}, parent_id={self.parent_id})"
