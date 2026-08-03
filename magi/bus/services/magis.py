"""Bus service: magis (MAGIS membership / admin / role queries; PG-backed)."""

from __future__ import annotations


class MagisService:
    """MAGIS Society read queries; PG-backed in production."""

    def __init__(self) -> None:
        pass

    def list_members(self, group_id: int) -> list:
        from magi.db.magis import open_magis_session
        from magi.db.models_magis_membership import MAGISMembership
        from sqlalchemy import select
        with open_magis_session() as session:
            return list(session.scalars(
                select(MAGISMembership).where(MAGISMembership.group_id == group_id)
            ).all())

    def can_route_a2a(self, *, from_magic_id: int, to_magic_id: int) -> bool:
        from magi.db.models_magis_membership import can_route_a2a
        return can_route_a2a(from_magic_id=from_magic_id, to_magic_id=to_magic_id)
