"""ORM table ``departments`` — the company org tree.

A self-referential tree encoded by ``parent_id``:
top-level departments have ``parent_id = NULL``; every
other department's parent is another department in the
same table. Cycles are prevented at the API layer
(POST/PATCH refuse a parent that would close a loop);
the schema doesn't enforce it because SQLite ignores
``CHECK (id != parent_id)`` constraints on insert anyway.

``manager_id`` references ``employees.id`` (a different
file — :mod:`magi.agent.db.models_employee`) and is
nullable — a department can exist without a manager
assigned yet.

The cross-table relationships (manager, employees)
point at :class:`Employee`. Same TYPE_CHECKING pattern
as :mod:`.models_employee` — FK strings resolve at
mapper-config time, after both modules are imported.
"""

from __future__ import annotations

from datetime import datetime

from magi.agent.db.base import utcnow_naive
from typing import TYPE_CHECKING

from sqlalchemy import (
    DateTime,
    ForeignKey,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from magi.agent.db.base import Base


if TYPE_CHECKING:
    from magi.agent.db.models_employee import Employee


class Department(Base):
    """A node in the company org tree.

    ``manager_id`` references ``employees.id`` and is nullable —
    a department can exist without a manager assigned yet.
    """

    __tablename__ = "departments"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("departments.id", ondelete="RESTRICT"),
        nullable=True,
    )
    manager_id: Mapped[int | None] = mapped_column(
        ForeignKey("employees.id", ondelete="SET NULL"),
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
    # departments rather than a back to the parent.
    children: Mapped[list["Department"]] = relationship(
        back_populates="parent",
        cascade="all, delete-orphan",
        single_parent=True,
    )
    parent: Mapped["Department | None"] = relationship(
        back_populates="children",
        remote_side="Department.id",
    )

    manager: Mapped["Employee | None"] = relationship(
        back_populates="led_department",
        foreign_keys=[manager_id],
    )

    # Employees that belong to this department. Backref
    # from ``Employee.department``. ``viewonly=True`` so
    # the Department endpoint doesn't accidentally mutate
    # employees via the collection.
    employees: Mapped[list["Employee"]] = relationship(
        back_populates="department",
        foreign_keys="Employee.department_id",
        viewonly=True,
    )

    def __repr__(self) -> str:
        return f"Department(id={self.id}, name={self.name!r}, parent_id={self.parent_id})"