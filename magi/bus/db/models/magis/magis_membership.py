
"""MAGIS roles and MAGI memberships.

Roles belong to one MAGIS. A membership is the MAGI's one direct MAGIS
home. MAGIS administration is direct-only; a parent/child tree relationship
does not add another MAGIS's instructions, workspace, or authority to ADAM.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from magi.bus.db.base import Base, utcnow_naive

RESERVED_ROLE_NAMES = frozenset({"ADAM", "EVA"})
DEFAULT_ROLE_INSTRUCTIONS = {
    "ADAM": "You are the team leader for this MAGIS. Coordinate work, clarify goals, and surface conflicts or risks to the administrator.",
    "EVA": "You are a general-purpose member of this MAGIS. Collaborate with the team, carry out assigned work carefully, and report blockers clearly.",
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

    __table_args__ = (UniqueConstraint("magic_id", name="uq_magis_memberships_magic"),)


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


def adam_manages_magis(session, magic_id: int, target_magis_id: int) -> bool:
    """Whether ``magic_id`` is the ADAM of exactly ``target_magis_id``.

    ADAM may administer child MAGIS from the WebUI in a future, separately
    granted capability, but tree ancestry must never silently give it a child
    MAGIS's data, instructions, or administrator scope.  The current access
    model is deliberately direct-only.
    """
    from magi.bus.db.models.magis.magis import MAGIS

    target = session.get(MAGIS, target_magis_id)
    return target is not None and target.adam_id == magic_id


def can_route_a2a(session, from_magic_id: int, to_magic_id: int) -> bool:
    """Apply MAGIS-tree routing rules for internal Agent-to-Agent messages.

    This is a message-routing rule, not WebUI authority or resource access:
    peers in one MAGIS may communicate; an ancestor MAGIS may delegate to a
    descendant's ADAM; that descendant ADAM may report to members of an
    ancestor MAGIS. Sibling MAGIS do not communicate directly.
    """
    from sqlalchemy import select
    from magi.bus.db.models.magis.magis import MAGIS

    memberships = {
        row.magic_id: row.magis_id
        for row in session.scalars(
            select(MAGISMembership).where(
                MAGISMembership.magic_id.in_((from_magic_id, to_magic_id))
            )
        )
    }
    source_id = memberships.get(from_magic_id)
    target_id = memberships.get(to_magic_id)
    if source_id is None or target_id is None:
        return False
    if source_id == target_id:
        return True

    target = session.get(MAGIS, target_id)
    if target is not None and target.adam_id == to_magic_id:
        current = target.parent_id
        while current is not None:
            if current == source_id:
                return True
            parent = session.get(MAGIS, current)
            current = parent.parent_id if parent is not None else None

    source = session.get(MAGIS, source_id)
    if source is not None and source.adam_id == from_magic_id:
        current = source.parent_id
        while current is not None:
            if current == target_id:
                return True
            parent = session.get(MAGIS, current)
            current = parent.parent_id if parent is not None else None
    return False
