"""MAGIS roles and MAGI memberships.

Roles belong to one MAGIS.  A membership assigns one MAGI to exactly one
role in that MAGIS, which lets a MAGI participate in several MAGIS without
turning Adam/EVE into global, mutually-exclusive MAGI types.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from magi.agent.db.base import Base, utcnow_naive

RESERVED_ROLE_NAMES = frozenset({"Adam", "EVE"})
DEFAULT_ROLE_INSTRUCTIONS = {
    "Adam": "You are the team leader for this MAGIS. Coordinate work, clarify goals, and surface conflicts or risks to the administrator.",
    "EVE": "You are a general-purpose member of this MAGIS. Collaborate with the team, carry out assigned work carefully, and report blockers clearly.",
}


class MAGISRole(Base):
    __tablename__ = "magis_roles"

    id: Mapped[int] = mapped_column(primary_key=True)
    magis_id: Mapped[int] = mapped_column(ForeignKey("magis.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    instruction: Mapped[str] = mapped_column(Text, nullable=False, default="")
    is_reserved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, onupdate=utcnow_naive, nullable=False)

    __table_args__ = (UniqueConstraint("magis_id", "name", name="uq_magis_roles_magis_name"),)


class MAGISMembership(Base):
    __tablename__ = "magis_memberships"

    id: Mapped[int] = mapped_column(primary_key=True)
    magis_id: Mapped[int] = mapped_column(ForeignKey("magis.id", ondelete="CASCADE"), nullable=False, index=True)
    magic_id: Mapped[int] = mapped_column(ForeignKey("magic.id", ondelete="CASCADE"), nullable=False, index=True)
    role_id: Mapped[int] = mapped_column(ForeignKey("magis_roles.id", ondelete="RESTRICT"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, onupdate=utcnow_naive, nullable=False)

    __table_args__ = (UniqueConstraint("magis_id", "magic_id", name="uq_magis_memberships_magis_magic"),)


def ensure_default_roles(session, magis_id: int) -> dict[str, MAGISRole]:
    """Create and return the two immutable built-in roles for a MAGIS."""
    from sqlalchemy import select

    roles = {r.name: r for r in session.scalars(select(MAGISRole).where(MAGISRole.magis_id == magis_id)).all()}
    for name, instruction in DEFAULT_ROLE_INSTRUCTIONS.items():
        if name not in roles:
            role = MAGISRole(magis_id=magis_id, name=name, instruction=instruction, is_reserved=True)
            session.add(role)
            session.flush()
            roles[name] = role
    return roles
