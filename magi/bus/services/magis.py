"""Bus service: magis (MAGIS membership / admin / role queries; PG-backed)."""

from __future__ import annotations

from magi.bus.contracts.magis import (
    MagisAdminView,
    MagisMembershipView,
    MagisRoleView,
    MagisView,
)


def _iso(dt) -> str | None:
    return dt.isoformat() if dt is not None else None


class MagisService:
    """MAGIS Society read queries; PG-backed in production."""

    def __init__(self) -> None:
        pass

    def list_members(self, group_id: int) -> list:
        from magi.db.magis import open_magis_session
        from magi.bus.models.magis.magis_membership import MAGISMembership
        from sqlalchemy import select
        with open_magis_session() as session:
            return list(session.scalars(
                select(MAGISMembership).where(MAGISMembership.group_id == group_id)
            ).all())

    def can_route_a2a(self, *, from_magic_id: int, to_magic_id: int) -> bool:
        from magi.bus.models.magis.magis_membership import can_route_a2a
        return can_route_a2a(from_magic_id=from_magic_id, to_magic_id=to_magic_id)

    def is_control_admin(self, uid: int) -> bool:
        from magi.db import ControlOperator
        from magi.db.magis import open_magis_session
        with open_magis_session() as session:
            operator = session.get(ControlOperator, uid)
            return operator is not None and bool(operator.admin)

    # === MAGIS CRUD ===

    def list_magis(self) -> list[MagisView]:
        from magi.db.magis import open_magis_session
        from magi.bus.models.magis.magis import MAGIS
        from sqlalchemy import select
        with open_magis_session() as session:
            rows = session.scalars(select(MAGIS).order_by(MAGIS.name)).all()
            return [
                MagisView(
                    id=row.id,
                    name=row.name,
                    parent_id=row.parent_id,
                    instruction=row.instruction,
                    created_at=_iso(row.created_at),
                    updated_at=_iso(row.updated_at),
                )
                for row in rows
            ]

    def get_magis(self, group_id: int) -> MagisView | None:
        from magi.db.magis import open_magis_session
        from magi.bus.models.magis.magis import MAGIS
        with open_magis_session() as session:
            row = session.get(MAGIS, group_id)
            if row is None:
                return None
            return MagisView(
                id=row.id,
                name=row.name,
                parent_id=row.parent_id,
                instruction=row.instruction,
                created_at=_iso(row.created_at),
                updated_at=_iso(row.updated_at),
            )

    def create_magis(self, name: str, instruction: str, parent_id: int | None) -> MagisView:
        from magi.db.magis import open_magis_session
        from magi.bus.models.magis.magis import MAGIS
        with open_magis_session() as session:
            magis = MAGIS(name=name, parent_id=parent_id, instruction=instruction)
            session.add(magis)
            session.commit()
            session.refresh(magis)
            return MagisView(
                id=magis.id,
                name=magis.name,
                parent_id=magis.parent_id,
                instruction=magis.instruction,
                created_at=_iso(magis.created_at),
                updated_at=_iso(magis.updated_at),
            )

    def update_magis(self, group_id: int, name: str, instruction: str) -> MagisView:
        from magi.db.magis import open_magis_session
        from magi.bus.models.magis.magis import MAGIS
        with open_magis_session() as session:
            row = session.get(MAGIS, group_id)
            if row is None:
                raise LookupError(f"MAGIS {group_id} not found")
            row.name = name
            row.instruction = instruction
            session.commit()
            session.refresh(row)
            return MagisView(
                id=row.id,
                name=row.name,
                parent_id=row.parent_id,
                instruction=row.instruction,
                created_at=_iso(row.created_at),
                updated_at=_iso(row.updated_at),
            )

    def delete_magis(self, group_id: int) -> bool:
        from magi.db.magis import open_magis_session
        from magi.bus.models.magis.magis import MAGIS
        from sqlalchemy import update as sa_update
        with open_magis_session() as session:
            row = session.get(MAGIS, group_id)
            if row is None:
                return False
            session.execute(
                sa_update(MAGIS).where(MAGIS.parent_id == group_id).values(parent_id=row.parent_id)
            )
            session.delete(row)
            session.commit()
            return True

    # === Admin CRUD ===

    def list_admins(self, group_id: int) -> list[MagisAdminView]:
        from magi.db.magis import open_magis_session
        from magi.bus.models.magis.magis_admin import MAGISAdmin
        from sqlalchemy import select
        with open_magis_session() as session:
            rows = session.scalars(
                select(MAGISAdmin).where(MAGISAdmin.magis_id == group_id).order_by(MAGISAdmin.id)
            ).all()
            return [
                MagisAdminView(
                    id=row.id,
                    group_id=row.magis_id,
                    # ORM column is telegram_id; the DTO field is named
                    # magic_id per the contract. Same value, different label.
                    magic_id=row.telegram_id,
                    created_at=_iso(row.created_at),
                )
                for row in rows
            ]

    def add_admin(self, group_id: int, magic_id: int, role_id: int) -> MagisAdminView:
        """Add (or refresh) an admin row for ``group_id``.

        ``magic_id`` populates the ORM's ``telegram_id`` column (the only
        identifier field on ``MAGISAdmin``). ``role_id`` is recorded as the
        ``display_name`` placeholder — no dedicated role column exists yet on
        the ORM model.
        """
        from magi.db.magis import open_magis_session
        from magi.bus.models.magis.magis_admin import MAGISAdmin
        from sqlalchemy import select
        with open_magis_session() as session:
            existing = session.scalar(
                select(MAGISAdmin).where(
                    MAGISAdmin.magis_id == group_id,
                    MAGISAdmin.telegram_id == magic_id,
                )
            )
            if existing is not None:
                row = existing
                if row.display_name is None:
                    row.display_name = str(role_id)
                    session.commit()
                    session.refresh(row)
            else:
                row = MAGISAdmin(
                    magis_id=group_id,
                    telegram_id=magic_id,
                    display_name=str(role_id),
                )
                session.add(row)
                session.commit()
                session.refresh(row)
            return MagisAdminView(
                id=row.id,
                group_id=row.magis_id,
                magic_id=row.telegram_id,
                created_at=_iso(row.created_at),
            )

    def remove_admin(self, group_id: int, magic_id: int) -> bool:
        from magi.db.magis import open_magis_session
        from magi.bus.models.magis.magis_admin import MAGISAdmin
        from sqlalchemy import select
        with open_magis_session() as session:
            row = session.scalar(
                select(MAGISAdmin).where(
                    MAGISAdmin.magis_id == group_id,
                    MAGISAdmin.telegram_id == magic_id,
                )
            )
            if row is None:
                return False
            session.delete(row)
            session.commit()
            return True

    # === Role CRUD ===

    def list_roles(self) -> list[MagisRoleView]:
        from magi.db.magis import open_magis_session
        from magi.bus.models.magis.magis_membership import MAGISRole
        from sqlalchemy import select
        with open_magis_session() as session:
            rows = session.scalars(
                select(MAGISRole).order_by(MAGISRole.magis_id, MAGISRole.name)
            ).all()
            return [
                MagisRoleView(
                    id=row.id,
                    name=row.name,
                    instruction=row.instruction,
                    created_at=_iso(row.created_at),
                )
                for row in rows
            ]

    def create_role(self, name: str, instruction: str) -> MagisRoleView:
        """Create a role attached to the root MAGIS.

        The ORM requires a non-null ``magis_id``; the contract intentionally
        omits it, so this method routes new roles to the root society until
        callers gain an explicit scope.
        """
        from magi.db.magis import open_magis_session
        from magi.bus.models.magis.magis import MAGIS
        from magi.bus.models.magis.magis_membership import MAGISRole
        from sqlalchemy import select
        with open_magis_session() as session:
            root = session.scalar(select(MAGIS).where(MAGIS.parent_id.is_(None)).order_by(MAGIS.id))
            if root is None:
                raise LookupError("no root MAGIS available to host a new role")
            role = MAGISRole(magis_id=root.id, name=name, instruction=instruction)
            session.add(role)
            session.commit()
            session.refresh(role)
            return MagisRoleView(
                id=role.id,
                name=role.name,
                instruction=role.instruction,
                created_at=_iso(role.created_at),
            )

    def update_role(self, role_id: int, name: str, instruction: str) -> MagisRoleView:
        from magi.db.magis import open_magis_session
        from magi.bus.models.magis.magis_membership import MAGISRole
        with open_magis_session() as session:
            row = session.get(MAGISRole, role_id)
            if row is None:
                raise LookupError(f"role {role_id} not found")
            row.name = name
            row.instruction = instruction
            session.commit()
            session.refresh(row)
            return MagisRoleView(
                id=row.id,
                name=row.name,
                instruction=row.instruction,
                created_at=_iso(row.created_at),
            )

    def delete_role(self, role_id: int) -> bool:
        from magi.db.magis import open_magis_session
        from magi.bus.models.magis.magis_membership import MAGISMembership, MAGISRole
        from sqlalchemy import select
        with open_magis_session() as session:
            row = session.get(MAGISRole, role_id)
            if row is None:
                return False
            in_use = session.scalar(select(MAGISMembership.id).where(MAGISMembership.role_id == role_id))
            if in_use is not None:
                raise ValueError(f"role {role_id} is in use by a membership")
            session.delete(row)
            session.commit()
            return True

    # === Membership CRUD ===

    def list_memberships(self, group_id: int) -> list[MagisMembershipView]:
        from magi.db.magis import open_magis_session
        from magi.bus.models.magis.magis_membership import MAGISMembership
        from sqlalchemy import select
        with open_magis_session() as session:
            rows = session.scalars(
                select(MAGISMembership).where(MAGISMembership.magis_id == group_id).order_by(MAGISMembership.id)
            ).all()
            return [
                MagisMembershipView(
                    id=row.id,
                    magic_id=row.magic_id,
                    group_id=row.magis_id,
                    role_id=row.role_id,
                    created_at=_iso(row.created_at),
                )
                for row in rows
            ]

    def add_membership(self, magic_id: int, group_id: int, role_id: int) -> MagisMembershipView:
        from magi.db.magis import open_magis_session
        from magi.bus.models.magis.magis_membership import MAGISMembership
        with open_magis_session() as session:
            row = MAGISMembership(magis_id=group_id, magic_id=magic_id, role_id=role_id)
            session.add(row)
            session.commit()
            session.refresh(row)
            return MagisMembershipView(
                id=row.id,
                magic_id=row.magic_id,
                group_id=row.magis_id,
                role_id=row.role_id,
                created_at=_iso(row.created_at),
            )

    def remove_membership(self, membership_id: int) -> bool:
        from magi.db.magis import open_magis_session
        from magi.bus.models.magis.magis_membership import MAGISMembership
        with open_magis_session() as session:
            row = session.get(MAGISMembership, membership_id)
            if row is None:
                return False
            session.delete(row)
            session.commit()
            return True