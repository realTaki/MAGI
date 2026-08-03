"""BUS service for public MAGIC runtime identity and configuration."""

from __future__ import annotations

import os
from typing import Any

from magi.bus.contracts.magis import ProviderConfiguration


class MagicService:
    """Public MAGI Society runtime registry; PG-backed in production."""

    def __init__(self, state_dir: str | None = None) -> None:
        self._state_dir = state_dir

    @staticmethod
    def _runtime_magic(session: Any):
        """Resolve the runtime MAGIC row inside the BUS-owned PG session."""
        from sqlalchemy import select

        from magi.bus.models.magis.magic import MAGIC
        from magi.bus.models.magis.magis import MAGIS

        runtime_id = os.environ.get("MAGI_RUNTIME_ID")
        if runtime_id and runtime_id.isdigit():
            return session.get(MAGIC, int(runtime_id))
        root = session.scalar(select(MAGIS).where(MAGIS.parent_id.is_(None)).order_by(MAGIS.id))
        return session.get(MAGIC, root.adam_id) if root and root.adam_id else None

    def provider_configuration(self) -> ProviderConfiguration | None:
        from magi.db.magis import open_magis_session

        with open_magis_session() as session:
            magic = self._runtime_magic(session)
            if magic is None or not magic.provider or not magic.api_key:
                return None
            return ProviderConfiguration(
                provider=str(magic.provider),
                api_key=str(magic.api_key),
                model=getattr(magic, "model", None),
            )

    def instruction_context(self) -> tuple[str, list[dict[str, str]]]:
        """Return only serialisable instruction facts for the active MAGIC."""
        from sqlalchemy import select

        from magi.db.magis import open_magis_session
        from magi.bus.models.magis.magis import MAGIS
        from magi.bus.models.magis.magis_membership import MAGISMembership, MAGISRole

        with open_magis_session() as session:
            magic = self._runtime_magic(session)
            if magic is None:
                return "", []
            row = session.execute(
                select(MAGISMembership, MAGISRole, MAGIS)
                .join(MAGISRole, MAGISRole.id == MAGISMembership.role_id)
                .join(MAGIS, MAGIS.id == MAGISMembership.magis_id)
                .where(MAGISMembership.magic_id == magic.id)
                .order_by(MAGISMembership.id)
            ).first()
            memberships = [] if row is None else [{
                "magis_name": str(row[2].name),
                "team_instruction": str(row[2].instruction or ""),
                "role_name": str(row[1].name),
                "role_instruction": str(row[1].instruction or ""),
            }]
            return str(magic.instruction or ""), memberships

    def can_receive_a2a(self, sender_magic_id: int) -> bool:
        """Apply MAGIS routing policy for ingress to this runtime."""
        from magi.db.magis import open_magis_session
        from magi.bus.models.magis.magis_membership import can_route_a2a

        runtime_id = os.environ.get("MAGI_RUNTIME_ID")
        if not runtime_id or not runtime_id.isdigit():
            return False
        with open_magis_session() as session:
            return can_route_a2a(session, sender_magic_id, int(runtime_id))
