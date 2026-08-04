"""Bus service: magis (MAGIS membership / admin / role queries; PG-backed)."""

from __future__ import annotations

import os
from typing import Any

from magi.bus.contracts.magis import (
    MagisAdminView,
    MagisMembershipView,
    MagisRoleView,
    MagisView,
    OperatorView,
)


# Sentinel for partial-update kwargs (matches the magic service).
_FIELD_UNSET: Any = object()


def _iso(dt) -> str | None:
    return dt.isoformat() if dt is not None else None


def _count_children(session: Any, parent_id: int) -> tuple[int, ...]:
    from sqlalchemy import select

    from magi.bus.models.magis.magis import MAGIS

    rows = session.scalars(
        select(MAGIS.id).where(MAGIS.parent_id == parent_id).order_by(MAGIS.id)
    ).all()
    return tuple(int(r) for r in rows)


def _count_members(session: Any, magis_id: int) -> int:
    from sqlalchemy import func, select

    from magi.bus.models.magis.magis_membership import MAGISMembership

    return int(
        session.scalar(
            select(func.count())
            .select_from(MAGISMembership)
            .where(MAGISMembership.magis_id == magis_id)
        )
        or 0
    )


def _magis_view(session: Any, row: Any) -> MagisView:
    return MagisView(
        id=int(row.id),
        name=str(row.name),
        parent_id=row.parent_id,
        adam_id=row.adam_id,
        instruction=row.instruction or "",
        child_ids=_count_children(session, int(row.id)),
        member_count=_count_members(session, int(row.id)),
        created_at=_iso(row.created_at),
        updated_at=_iso(row.updated_at),
    )


def _role_view(row: Any) -> MagisRoleView:
    return MagisRoleView(
        id=int(row.id),
        magis_id=int(row.magis_id),
        name=str(row.name),
        instruction=row.instruction or "",
        is_reserved=bool(row.is_reserved),
        created_at=_iso(row.created_at),
    )


def _admin_view(row: Any) -> MagisAdminView:
    return MagisAdminView(
        id=int(row.id),
        group_id=int(row.magis_id),
        magic_id=int(row.telegram_id),
        display_name=row.display_name,
        created_at=_iso(row.created_at),
    )


def _membership_view(row: Any, magic_name: Any, role_name: Any) -> MagisMembershipView:
    return MagisMembershipView(
        id=int(row.id),
        magic_id=int(row.magic_id),
        magic_name=None if magic_name is None else str(magic_name),
        group_id=int(row.magis_id),
        role_id=int(row.role_id),
        role_name=str(role_name),
        created_at=_iso(row.created_at),
    )


class MagisService:
    """MAGIS Society read queries; PG-backed in production."""

    def __init__(self) -> None:
        pass

    # ------------------------------------------------------------------
    # Runtime identity helpers — what this WebUI is bound to.
    # ------------------------------------------------------------------

    def current_runtime_magic_id(self) -> int | None:
        """Return the ``MAGI`` id served by this WebUI, or ``None``.

        Reads ``MAGI_RUNTIME_ID`` first; falls back to the root
        MAGIS's ``adam_id``.  Used by the WebUI to resolve which
        MAGI it operates as; channels must not introspect
        ``MAGIS`` rows themselves for this.
        """
        runtime_id = os.environ.get("MAGI_RUNTIME_ID")
        if runtime_id and runtime_id.isdigit():
            return int(runtime_id)
        from magi.bus.db.magis import open_magis_session
        from magi.bus.models.magis.magis import MAGIS
        from sqlalchemy import select

        with open_magis_session() as session:
            root = session.scalar(
                select(MAGIS).where(MAGIS.parent_id.is_(None)).order_by(MAGIS.id)
            )
            return int(root.adam_id) if root and root.adam_id else None

    def derive_runtime_role(self, magic_id: int) -> str:
        """Return ``"adam"`` if ``magic_id`` is the ADAM of its MAGIS, else ``"eva"``.

        Looks up the MAGIC's direct MAGIS membership and compares its
        ``id`` against the membership's ``MAGIS.adam_id``. This is the
        single source of truth for the runtime's archetype — the boot
        flow never reads ``MAGI_NODE_ROLE``.
        """
        from magi.bus.db.magis import open_magis_session
        from magi.bus.models.magis.magis import MAGIS
        from magi.bus.models.magis.magis_membership import MAGISMembership
        from sqlalchemy import select

        with open_magis_session() as session:
            binding = session.scalar(
                select(MAGISMembership).where(MAGISMembership.magic_id == magic_id)
            )
            if binding is None:
                raise LookupError(
                    f"MAGI {magic_id} has no direct MAGIS membership; "
                    "the orchestrator must create the MAGIC + membership row "
                    "before starting the runtime"
                )
            magis = session.get(MAGIS, binding.magis_id)
            if magis is None:
                raise LookupError(f"MAGIS {binding.magis_id} not found")
            return "adam" if int(magis.adam_id) == magic_id else "eva"

    def served_direct_magis_id(self) -> int | None:
        """The single MAGIS id this WebUI may administer directly.

        Resolves to the runtime MAGIC's first MAGISMembership if it
        has one; otherwise falls back to the root MAGIS.  The bus
        does not police "may administer" beyond this — the WebUI
        layer compares this id against the requested magis_id.
        """
        from magi.bus.db.magis import open_magis_session
        from magi.bus.models.magis.magis import MAGIS
        from magi.bus.models.magis.magis_membership import MAGISMembership
        from sqlalchemy import select

        served_magic_id = self.current_runtime_magic_id()
        with open_magis_session() as session:
            if served_magic_id is not None:
                binding = session.scalar(
                    select(MAGISMembership).where(
                        MAGISMembership.magic_id == served_magic_id
                    )
                )
                if binding is not None:
                    return int(binding.magis_id)
            root = session.scalar(
                select(MAGIS).where(MAGIS.parent_id.is_(None)).order_by(MAGIS.id)
            )
            return int(root.id) if root else None

    def assigned_magic_ids(self) -> set[int]:
        """Set of every MAGIC id that is bound to *any* MAGIS.

        Used by ``MagicService.list_magic`` to distinguish
        "unassigned" MAGIs from those already routed through a
        MAGIS.  Walks every MAGIS once — N+1 by design, N is the
        number of MAGISes (small), so the cost is bounded.
        """
        ids: set[int] = set()
        for magis in self.list_magis():
            ids.update(view.magic_id for view in self.list_memberships(magis.id))
        return ids

    def direct_magis_binding_for_magic(self, magic_id: int) -> list[MagisMembershipView]:
        """List this MAGIC's direct MAGIS bindings with role/magis names.

        Convenience wrapper around :meth:`list_memberships_for_magic`
        for API handlers that need the role/magis context to render
        a card or to build an orchestrator ``EvaSpec``.
        """
        return self.list_memberships_for_magic(magic_id)

    # ------------------------------------------------------------------
    # Operator (control-plane admin) lookup.
    # ------------------------------------------------------------------

    def is_control_admin(self, uid: int) -> bool:
        from magi.bus.models.local.control_plane import ControlOperator
        from magi.bus.db.magis import open_magis_session
        with open_magis_session() as session:
            operator = session.get(ControlOperator, uid)
            return operator is not None and bool(operator.admin)

    def list_control_operators(self, *, admin_only: bool = False) -> list[OperatorView]:
        from magi.bus.models.local.control_plane import ControlOperator
        from magi.bus.db.magis import open_magis_session
        from sqlalchemy import select

        with open_magis_session() as session:
            stmt = select(ControlOperator).order_by(ControlOperator.id)
            if admin_only:
                stmt = stmt.where(ControlOperator.admin.is_(True))
            rows = session.scalars(stmt).all()
            return [
                OperatorView(
                    id=int(row.id),
                    telegram_id=int(row.telegram_id),
                    display_name=row.display_name,
                    admin=bool(row.admin),
                    created_at=_iso(row.created_at),
                    updated_at=_iso(row.updated_at),
                )
                for row in rows
            ]

    def get_control_operator(self, uid: int) -> OperatorView | None:
        from magi.bus.models.local.control_plane import ControlOperator
        from magi.bus.db.magis import open_magis_session
        with open_magis_session() as session:
            operator = session.get(ControlOperator, uid)
            if operator is None:
                return None
            return OperatorView(
                id=int(operator.id),
                telegram_id=int(operator.telegram_id),
                display_name=operator.display_name,
                admin=bool(operator.admin),
                created_at=_iso(operator.created_at),
                updated_at=_iso(operator.updated_at),
            )

    # ------------------------------------------------------------------
    # MAGIS root lookup.
    # ------------------------------------------------------------------

    def get_root_magis_id(self) -> int | None:
        from magi.bus.db.magis import open_magis_session
        from magi.bus.models.magis.magis import MAGIS
        from sqlalchemy import select
        with open_magis_session() as session:
            return session.scalar(
                select(MAGIS.id).where(MAGIS.parent_id.is_(None)).order_by(MAGIS.id)
            )

    def get_root_magis(self) -> MagisView | None:
        """Return the root MAGIS (``parent_id IS NULL``) as a value object.

        The WebUI ``/available-magi`` endpoint uses ``adam_id`` to decide
        which MAGICs are sign-in candidates; the bus exposes the full
        DTO so callers don't have to reach into the ORM.
        """
        from magi.bus.db.magis import open_magis_session
        from magi.bus.models.magis.magis import MAGIS
        from sqlalchemy import select
        with open_magis_session() as session:
            root = session.scalar(
                select(MAGIS).where(MAGIS.parent_id.is_(None)).order_by(MAGIS.id)
            )
            if root is None:
                return None
            return _magis_view(session, root)

    def runtime_url_for_magic(self, magic_id: int) -> str:
        """Return the http(s) URL for a MAGIC's runtime, or raise ``RuntimeError``.

        Mirrors the pre-refactor helper in
        :mod:`magi.channels.api.runtime_proxy` so the WebUI
        target-login flow has one place to ask for the upstream URL
        of a selected MAGIC.  Used by the channels layer to forward
        the pre-login /api/access/* call; ``RuntimeError`` is mapped
        to a 503 by the caller.
        """
        from magi.bus.models.magis.eva_runtime import EvaRuntime
        from magi.bus.models.magis.magic import MAGIC
        from magi.bus.models.magis.magis import MAGIS
        from magi.bus.db.magis import open_magis_session
        from sqlalchemy import select
        import os
        with open_magis_session() as session:
            magic = session.get(MAGIC, magic_id)
            if magic is None:
                raise RuntimeError(f"MAGIC {magic_id} not found")
            runtime = session.scalar(
                select(EvaRuntime).where(EvaRuntime.magic_id == magic_id)
            )
            if runtime and runtime.deployment_name and runtime.observed_state not in {"stopped", "deleted"}:
                return f"http://{runtime.deployment_name}:42069"
            root = session.scalar(select(MAGIS).where(MAGIS.parent_id.is_(None)).order_by(MAGIS.id))
            if root and root.adam_id == magic_id:
                return os.environ.get("MAGI_ROOT_RUNTIME_URL", "http://magi:42069")
        raise RuntimeError(f"MAGIC {magic_id} is not running")

    def replace_admins(
        self, group_id: int, telegram_ids_with_names: list[tuple[int, str | None]],
    ) -> list[MagisAdminView]:
        from magi.bus.db.magis import open_magis_session
        from magi.bus.models.magis.magis_admin import MAGISAdmin
        from sqlalchemy import select
        with open_magis_session() as session:
            existing = session.scalars(
                select(MAGISAdmin).where(MAGISAdmin.magis_id == group_id)
            ).all()
            wanted_ids = {tg_id for tg_id, _name in telegram_ids_with_names}
            for operator in existing:
                if operator.telegram_id not in wanted_ids:
                    session.delete(operator)
            known = {operator.telegram_id: operator for operator in existing}
            for telegram_id, display_name in telegram_ids_with_names:
                if telegram_id in known:
                    operator = known[telegram_id]
                else:
                    operator = MAGISAdmin(
                        magis_id=group_id,
                        telegram_id=telegram_id,
                        display_name=display_name or f"Admin {telegram_id}",
                    )
                    session.add(operator)
            session.commit()
            rows = session.scalars(
                select(MAGISAdmin).where(MAGISAdmin.magis_id == group_id).order_by(MAGISAdmin.id)
            ).all()
            return [_admin_view(row) for row in rows]

    # ------------------------------------------------------------------
    # MAGIS CRUD.
    # ------------------------------------------------------------------

    def list_magis(self) -> list[MagisView]:
        """Return every MAGIS row with full counts populated."""
        from magi.bus.db.magis import open_magis_session
        from magi.bus.models.magis.magis import MAGIS
        from sqlalchemy import select
        with open_magis_session() as session:
            rows = session.scalars(select(MAGIS).order_by(MAGIS.id)).all()
            return [_magis_view(session, row) for row in rows]

    def get_magis(self, group_id: int) -> MagisView | None:
        from magi.bus.db.magis import open_magis_session
        from magi.bus.models.magis.magis import MAGIS
        with open_magis_session() as session:
            row = session.get(MAGIS, group_id)
            if row is None:
                return None
            return _magis_view(session, row)

    def name_exists(self, name: str, exclude_id: int | None = None) -> bool:
        from magi.bus.db.magis import open_magis_session
        from magi.bus.models.magis.magis import MAGIS
        from sqlalchemy import select
        with open_magis_session() as session:
            found = session.scalar(select(MAGIS.id).where(MAGIS.name == name))
            if found is None:
                return False
            return exclude_id is None or int(found) != int(exclude_id)

    def create_magis(
        self,
        name: str,
        instruction: str,
        parent_id: int | None,
    ) -> MagisView:
        """Insert a MAGIS row, seed its default ADAM/EVA roles.

        Raises ``ValueError`` on a name collision (the caller maps it
        to a 400).  Uses the bus-owned default-role seed so the
        reserved ADAM/EVA pair exists before the first member is
        added.
        """
        from magi.bus.db.magis import open_magis_session
        from magi.bus.models.magis.magis import MAGIS
        from magi.bus.models.magis.magis_membership import ensure_default_roles
        with open_magis_session() as session:
            if session.scalar(
                __import__("sqlalchemy").select(MAGIS.id).where(MAGIS.name == name)
            ):
                raise ValueError(f"MAGIS name {name!r} already exists")
            magis = MAGIS(name=name, parent_id=parent_id, instruction=instruction)
            session.add(magis)
            session.flush()
            ensure_default_roles(session, magis.id)
            session.commit()
            session.refresh(magis)
            return _magis_view(session, magis)

    def update_magis(
        self,
        group_id: int,
        *,
        name: Any = _FIELD_UNSET,
        instruction: Any = _FIELD_UNSET,
        parent_id: Any = _FIELD_UNSET,
    ) -> MagisView:
        """Partial update of a MAGIS row.

        Each kwarg defaults to ``_FIELD_UNSET``; pass ``None``
        explicitly to clear ``instruction`` or ``parent_id``.
        Validates ``parent_id`` assignments: refuse self-parent
        and cycles through descendants.  Raises ``LookupError``
        when the MAGIS is missing, ``ValueError`` on duplicate
        name / self-parent / cycle.
        """
        from sqlalchemy import select, update as sa_update

        from magi.bus.models.magis.magis import MAGIS
        from magi.bus.db.magis import open_magis_session

        with open_magis_session() as session:
            row = session.get(MAGIS, group_id)
            if row is None:
                raise LookupError(f"MAGIS {group_id} not found")

            if name is not _FIELD_UNSET and name != row.name:
                duplicate = session.scalar(select(MAGIS.id).where(MAGIS.name == name))
                if duplicate is not None and int(duplicate) != int(group_id):
                    raise ValueError(f"MAGIS name {name!r} already exists")
                row.name = name

            if instruction is not _FIELD_UNSET:
                row.instruction = instruction or ""

            if parent_id is not _FIELD_UNSET:
                if parent_id == group_id:
                    raise ValueError("a MAGIS cannot be its own parent")
                if parent_id is not None:
                    if session.get(MAGIS, parent_id) is None:
                        raise LookupError(f"MAGIS parent {parent_id} not found")
                    # Detect cycle: walk up the proposed parent chain.
                    cursor = session.get(MAGIS, parent_id)
                    while cursor is not None:
                        if cursor.id == group_id:
                            raise ValueError(
                                "parent assignment would create a cycle"
                            )
                        cursor = (
                            session.get(MAGIS, cursor.parent_id)
                            if cursor.parent_id
                            else None
                        )
                row.parent_id = parent_id

            session.commit()
            session.refresh(row)
            return _magis_view(session, row)

    def delete_magis(self, group_id: int) -> bool:
        """Delete a MAGIS row after re-parenting its children to the parent.

        Returns ``True`` when the row existed and was deleted, ``False``
        otherwise.  Children are explicitly re-parented before the
        delete; the API never relies on ORM cascade.
        """
        from sqlalchemy import update as sa_update

        from magi.bus.models.magis.magis import MAGIS
        from magi.bus.db.magis import open_magis_session

        with open_magis_session() as session:
            row = session.get(MAGIS, group_id)
            if row is None:
                return False
            session.execute(
                sa_update(MAGIS)
                .where(MAGIS.parent_id == group_id)
                .values(parent_id=row.parent_id)
            )
            session.delete(row)
            session.commit()
            return True

    # ------------------------------------------------------------------
    # Admin CRUD.
    # ------------------------------------------------------------------

    def list_admins(self, group_id: int) -> list[MagisAdminView]:
        from magi.bus.db.magis import open_magis_session
        from magi.bus.models.magis.magis_admin import MAGISAdmin
        from sqlalchemy import select
        with open_magis_session() as session:
            rows = session.scalars(
                select(MAGISAdmin).where(MAGISAdmin.magis_id == group_id).order_by(MAGISAdmin.id)
            ).all()
            return [_admin_view(row) for row in rows]

    def add_admin_with_display(
        self,
        group_id: int,
        telegram_id: int,
        display_name: str | None,
    ) -> MagisAdminView:
        """Create (or update the display_name of) a MAGISAdmin row.

        Returns the persisted view.  Raises ``LookupError`` if the
        MAGIS does not exist.
        """
        from magi.bus.db.magis import open_magis_session
        from magi.bus.models.magis.magis import MAGIS
        from magi.bus.models.magis.magis_admin import MAGISAdmin
        from sqlalchemy import select
        with open_magis_session() as session:
            if session.get(MAGIS, group_id) is None:
                raise LookupError(f"MAGIS {group_id} not found")
            existing = session.scalar(
                select(MAGISAdmin).where(
                    MAGISAdmin.magis_id == group_id,
                    MAGISAdmin.telegram_id == telegram_id,
                )
            )
            if existing is not None:
                if display_name is not None:
                    existing.display_name = display_name
                    session.commit()
                    session.refresh(existing)
                row = existing
            else:
                row = MAGISAdmin(
                    magis_id=group_id,
                    telegram_id=telegram_id,
                    display_name=display_name,
                )
                session.add(row)
                session.commit()
                session.refresh(row)
            return _admin_view(row)

    def delete_admin_in_magis(self, group_id: int, admin_id: int) -> bool:
        """Delete a MAGIS admin by id, verifying it belongs to ``group_id``."""
        from magi.bus.db.magis import open_magis_session
        from magi.bus.models.magis.magis_admin import MAGISAdmin
        with open_magis_session() as session:
            row = session.get(MAGISAdmin, admin_id)
            if row is None or row.magis_id != group_id:
                return False
            session.delete(row)
            session.commit()
            return True

    def add_admin(self, group_id: int, magic_id: int, role_id: int) -> MagisAdminView:
        from magi.bus.db.magis import open_magis_session
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
            return _admin_view(row)

    def remove_admin(self, group_id: int, magic_id: int) -> bool:
        from magi.bus.db.magis import open_magis_session
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

    # ------------------------------------------------------------------
    # Role CRUD.
    # ------------------------------------------------------------------

    def list_roles(self) -> list[MagisRoleView]:
        """All roles across every MAGIS, ordered for the WebUI table."""
        from magi.bus.db.magis import open_magis_session
        from magi.bus.models.magis.magis_membership import MAGISRole
        from sqlalchemy import select
        with open_magis_session() as session:
            rows = session.scalars(
                select(MAGISRole).order_by(MAGISRole.magis_id, MAGISRole.name)
            ).all()
            return [_role_view(row) for row in rows]

    def list_roles_in_magis(self, magis_id: int) -> list[MagisRoleView]:
        """Roles for one MAGIS, ordered reserved-first then by name."""
        from magi.bus.db.magis import open_magis_session
        from magi.bus.models.magis.magis_membership import MAGISRole
        from sqlalchemy import select

        from magi.bus.models.magis.magis_membership import ensure_default_roles
        with open_magis_session() as session:
            ensure_default_roles(session, magis_id)
            session.commit()
            rows = session.scalars(
                select(MAGISRole)
                .where(MAGISRole.magis_id == magis_id)
                .order_by(MAGISRole.is_reserved.desc(), MAGISRole.name)
            ).all()
            return [_role_view(row) for row in rows]

    def get_role_in_magis(self, magis_id: int, role_id: int) -> MagisRoleView | None:
        from magi.bus.db.magis import open_magis_session
        from magi.bus.models.magis.magis_membership import MAGISRole
        with open_magis_session() as session:
            row = session.get(MAGISRole, role_id)
            if row is None or row.magis_id != magis_id:
                return None
            return _role_view(row)

    def create_role_in_magis(
        self,
        magis_id: int,
        name: str,
        instruction: str,
    ) -> MagisRoleView:
        from magi.bus.models.magis.magis_membership import RESERVED_ROLE_NAMES

        from magi.bus.db.magis import open_magis_session
        from magi.bus.models.magis.magis import MAGIS
        from magi.bus.models.magis.magis_membership import MAGISRole
        from sqlalchemy import select

        if name in RESERVED_ROLE_NAMES:
            raise ValueError("ADAM and EVA are reserved role names")
        with open_magis_session() as session:
            if session.get(MAGIS, magis_id) is None:
                raise LookupError(f"MAGIS {magis_id} not found")
            duplicate = session.scalar(
                select(MAGISRole.id).where(
                    MAGISRole.magis_id == magis_id,
                    MAGISRole.name == name,
                )
            )
            if duplicate is not None:
                raise ValueError("role name already exists in this MAGIS")
            role = MAGISRole(magis_id=magis_id, name=name, instruction=instruction)
            session.add(role)
            session.commit()
            session.refresh(role)
            return _role_view(role)

    def update_role_in_magis(
        self,
        magis_id: int,
        role_id: int,
        *,
        name: Any = _FIELD_UNSET,
        instruction: Any = _FIELD_UNSET,
    ) -> MagisRoleView:
        from magi.bus.db.magis import open_magis_session
        from magi.bus.models.magis.magis_membership import MAGISRole
        with open_magis_session() as session:
            row = session.get(MAGISRole, role_id)
            if row is None or row.magis_id != magis_id:
                raise LookupError(f"role {role_id} not in MAGIS {magis_id}")
            if row.is_reserved:
                raise PermissionError("reserved roles cannot be edited")
            if name is not _FIELD_UNSET:
                row.name = name
            if instruction is not _FIELD_UNSET:
                row.instruction = instruction or ""
            session.commit()
            session.refresh(row)
            return _role_view(row)

    def delete_role_in_magis(self, magis_id: int, role_id: int) -> bool:
        from sqlalchemy import select

        from magi.bus.models.magis.magis_membership import (
            MAGISMembership,
            MAGISRole,
        )
        from magi.bus.db.magis import open_magis_session
        with open_magis_session() as session:
            row = session.get(MAGISRole, role_id)
            if row is None or row.magis_id != magis_id:
                return False
            if row.is_reserved:
                raise PermissionError("reserved roles cannot be deleted")
            in_use = session.scalar(
                select(MAGISMembership.id).where(MAGISMembership.role_id == role_id)
            )
            if in_use is not None:
                raise ValueError("reassign members before deleting this role")
            session.delete(row)
            session.commit()
            return True

    def create_role(self, name: str, instruction: str) -> MagisRoleView:
        from magi.bus.db.magis import open_magis_session
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
            return _role_view(role)

    def update_role(self, role_id: int, name: str, instruction: str) -> MagisRoleView:
        from magi.bus.db.magis import open_magis_session
        from magi.bus.models.magis.magis_membership import MAGISRole
        with open_magis_session() as session:
            row = session.get(MAGISRole, role_id)
            if row is None:
                raise LookupError(f"role {role_id} not found")
            row.name = name
            row.instruction = instruction
            session.commit()
            session.refresh(row)
            return _role_view(row)

    def delete_role(self, role_id: int) -> bool:
        from sqlalchemy import select

        from magi.bus.models.magis.magis_membership import (
            MAGISMembership,
            MAGISRole,
        )
        from magi.bus.db.magis import open_magis_session
        with open_magis_session() as session:
            row = session.get(MAGISRole, role_id)
            if row is None:
                return False
            in_use = session.scalar(
                select(MAGISMembership.id).where(MAGISMembership.role_id == role_id)
            )
            if in_use is not None:
                raise ValueError(f"role {role_id} is in use by a membership")
            session.delete(row)
            session.commit()
            return True

    # ------------------------------------------------------------------
    # Membership CRUD with ADAM/EVA-role side-effects.
    # ------------------------------------------------------------------

    def list_memberships(self, group_id: int) -> list[MagisMembershipView]:
        from sqlalchemy import select

        from magi.bus.models.magis.magis import MAGIS
        from magi.bus.models.magis.magis_membership import (
            MAGISMembership,
            MAGISRole,
        )
        from magi.bus.models.magis.magic import MAGIC
        from magi.bus.db.magis import open_magis_session

        with open_magis_session() as session:
            rows = session.execute(
                select(MAGISMembership, MAGIC.name, MAGISRole.name)
                .join(MAGIC, MAGIC.id == MAGISMembership.magic_id)
                .join(MAGISRole, MAGISRole.id == MAGISMembership.role_id)
                .where(MAGISMembership.magis_id == group_id)
                .order_by(MAGISMembership.id)
            ).all()
            return [
                _membership_view(m, magic_name, role_name)
                for m, magic_name, role_name in rows
            ]

    def list_memberships_for_magic(self, magic_id: int) -> list[MagisMembershipView]:
        from sqlalchemy import select

        from magi.bus.models.magis.magis_membership import (
            MAGISMembership,
            MAGISRole,
        )
        from magi.bus.models.magis.magic import MAGIC
        from magi.bus.db.magis import open_magis_session

        with open_magis_session() as session:
            rows = session.execute(
                select(MAGISMembership, MAGIC.name, MAGISRole.name)
                .outerjoin(MAGIC, MAGIC.id == MAGISMembership.magic_id)
                .join(MAGISRole, MAGISRole.id == MAGISMembership.role_id)
                .where(MAGISMembership.magic_id == magic_id)
                .order_by(MAGISMembership.id)
            ).all()
            return [
                _membership_view(m, magic_name, role_name)
                for m, magic_name, role_name in rows
            ]

    def magic_exists(self, magic_id: int) -> bool:
        from magi.bus.models.magis.magic import MAGIC
        from magi.bus.db.magis import open_magis_session
        with open_magis_session() as session:
            return session.get(MAGIC, magic_id) is not None

    def get_membership_in_magis(
        self, magis_id: int, membership_id: int
    ) -> MagisMembershipView | None:
        from sqlalchemy import select

        from magi.bus.models.magis.magis_membership import (
            MAGISMembership,
            MAGISRole,
        )
        from magi.bus.models.magis.magic import MAGIC
        from magi.bus.db.magis import open_magis_session
        with open_magis_session() as session:
            row = session.execute(
                select(MAGISMembership, MAGIC.name, MAGISRole.name)
                .outerjoin(MAGIC, MAGIC.id == MAGISMembership.magic_id)
                .join(MAGISRole, MAGISRole.id == MAGISMembership.role_id)
                .where(MAGISMembership.id == membership_id)
            ).first()
            if row is None or row[0].magis_id != magis_id:
                return None
            return _membership_view(row[0], row[1], row[2])

    def create_membership_in_magis(
        self,
        magis_id: int,
        magic_id: int,
        role_id: int,
    ) -> MagisMembershipView:
        """Assign ``magic_id`` to ``magis_id`` with ``role_id``.

        Side effects:
          * refuse to create a second direct MAGIS membership for
            ``magic_id`` (a MAGI has at most one direct MAGIS);
          * when the assigned role is named ``ADAM``, set
            ``MAGIS.adam_id = magic_id`` (overwriting any prior
            ADAM), and propagate that to the surrounding
            ``MAGISMembership`` row.
        """
        from sqlalchemy import select

        from magi.bus.models.magis.magis import MAGIS
        from magi.bus.models.magis.magis_membership import MAGISMembership
        from magi.bus.models.magis.magic import MAGIC
        from magi.bus.db.magis import open_magis_session

        with open_magis_session() as session:
            magis = session.get(MAGIS, magis_id)
            if magis is None:
                raise LookupError(f"MAGIS {magis_id} not found")
            role = self.get_role_in_magis(magis_id, role_id)
            if role is None:
                raise LookupError(f"role {role_id} not in MAGIS {magis_id}")
            magic = session.get(MAGIC, magic_id)
            if magic is None:
                raise LookupError(f"MAGIC {magic_id} not found")
            existing = session.scalar(
                select(MAGISMembership).where(MAGISMembership.magic_id == magic_id)
            )
            if existing is not None:
                raise ValueError("a MAGI can have only one direct MAGIS membership")
            if role.name == "ADAM":
                if magis.adam_id is not None and int(magis.adam_id) != int(magic_id):
                    raise ValueError("this MAGIS already has an ADAM")
                magis.adam_id = magic_id
            elif magis.adam_id == magic_id:
                magis.adam_id = None
            membership = MAGISMembership(
                magis_id=magis_id, magic_id=magic_id, role_id=role_id
            )
            session.add(membership)
            session.commit()
            session.refresh(membership)
            return _membership_view(membership, magic.name, role.name)

    def update_membership_role_in_magis(
        self,
        magis_id: int,
        membership_id: int,
        new_role_id: int,
    ) -> MagisMembershipView:
        from magi.bus.models.magis.magis import MAGIS
        from magi.bus.models.magis.magis_membership import MAGISMembership
        from magi.bus.models.magis.magic import MAGIC
        from magi.bus.db.magis import open_magis_session

        with open_magis_session() as session:
            magis = session.get(MAGIS, magis_id)
            if magis is None:
                raise LookupError(f"MAGIS {magis_id} not found")
            membership = session.get(MAGISMembership, membership_id)
            if membership is None or membership.magis_id != magis_id:
                raise LookupError("membership not found in MAGIS")
            role = self.get_role_in_magis(magis_id, new_role_id)
            if role is None:
                raise LookupError(f"role {new_role_id} not in MAGIS {magis_id}")
            magic = session.get(MAGIC, membership.magic_id)
            if role.name == "ADAM":
                if magis.adam_id is not None and int(magis.adam_id) != int(membership.magic_id):
                    raise ValueError("this MAGIS already has an ADAM")
                magis.adam_id = membership.magic_id
            elif magis.adam_id == membership.magic_id:
                magis.adam_id = None
            membership.role_id = new_role_id
            session.commit()
            return _membership_view(membership, magic.name if magic else None, role.name)

    def delete_membership_in_magis(
        self,
        magis_id: int,
        membership_id: int,
    ) -> bool:
        from magi.bus.models.magis.magis import MAGIS
        from magi.bus.models.magis.magis_membership import MAGISMembership
        from magi.bus.db.magis import open_magis_session
        with open_magis_session() as session:
            magis = session.get(MAGIS, magis_id)
            if magis is None:
                raise LookupError(f"MAGIS {magis_id} not found")
            membership = session.get(MAGISMembership, membership_id)
            if membership is None or membership.magis_id != magis_id:
                return False
            if magis.adam_id == membership.magic_id:
                magis.adam_id = None
            session.delete(membership)
            session.commit()
            return True

    def add_membership(self, magic_id: int, group_id: int, role_id: int) -> MagisMembershipView:
        from magi.bus.db.magis import open_magis_session
        from magi.bus.models.magis.magis_membership import MAGISMembership
        from magi.bus.models.magis.magic import MAGIC
        from magi.bus.models.magis.magis_membership import MAGISRole
        from sqlalchemy import select
        with open_magis_session() as session:
            row = MAGISMembership(magis_id=group_id, magic_id=magic_id, role_id=role_id)
            session.add(row)
            session.commit()
            session.refresh(row)
            magic_name = session.get(MAGIC, magic_id).name if session.get(MAGIC, magic_id) else None
            role_name = session.get(MAGISRole, role_id).name
            return _membership_view(row, magic_name, role_name)

    def remove_membership(self, membership_id: int) -> bool:
        from magi.bus.db.magis import open_magis_session
        from magi.bus.models.magis.magis_membership import MAGISMembership
        with open_magis_session() as session:
            row = session.get(MAGISMembership, membership_id)
            if row is None:
                return False
            session.delete(row)
            session.commit()
            return True

    # ------------------------------------------------------------------
    # Direct-MAGIS lookup for a runtime.
    # ------------------------------------------------------------------

    def direct_magis_for_magic(self, magic_id: int) -> tuple[int, int]:
        from magi.bus.models.magis.magis_membership import MAGISMembership
        from magi.bus.db.magis import open_magis_session
        from sqlalchemy import select
        with open_magis_session() as session:
            binding = session.scalar(
                select(MAGISMembership).where(MAGISMembership.magic_id == magic_id)
            )
            if binding is None:
                raise LookupError(f"MAGIC {magic_id} is not assigned to a MAGIS")
            return magic_id, int(binding.magis_id)

    def list_magis_admins(self, magis_id: int) -> list[dict]:
        """Return ``[{telegram_id, display_name}]`` for MAGIS administrators."""
        from magi.bus.models.magis.magis_admin import MAGISAdmin
        from magi.bus.db.magis import open_magis_session
        from sqlalchemy import select
        with open_magis_session() as session:
            rows = session.scalars(
                select(MAGISAdmin).where(MAGISAdmin.magis_id == magis_id)
            ).all()
            return [
                {"telegram_id": int(row.telegram_id), "display_name": row.display_name}
                for row in rows
            ]

    def get_magis_adam_url(self, magis_id: int, current_magic_id: int) -> tuple[int, str] | None:
        from magi.bus.models.magis.eva_runtime import EvaRuntime
        from magi.bus.models.magis.magis import MAGIS
        from magi.bus.db.magis import open_magis_session
        from sqlalchemy import select
        with open_magis_session() as session:
            magis = session.get(MAGIS, magis_id)
            if magis is None or magis.adam_id is None or magis.adam_id == current_magic_id:
                return None
            runtime = session.scalar(
                select(EvaRuntime).where(EvaRuntime.magic_id == magis.adam_id)
            )
            if runtime and runtime.deployment_name and runtime.observed_state not in {"stopped", "deleted"}:
                return magis.adam_id, f"http://{runtime.deployment_name}:42069"
            root = session.scalar(select(MAGIS).where(MAGIS.parent_id.is_(None)).order_by(MAGIS.id))
            if root and root.adam_id == magis.adam_id:
                return magis.adam_id, os.environ.get("MAGI_ROOT_RUNTIME_URL", "http://magi:42069")
        return None

    # ------------------------------------------------------------------
    # WebUI control-plane KV.
    # ------------------------------------------------------------------

    def root_runtime_url(self, magic_id: int) -> str | None:
        from sqlalchemy import select

        from magi.bus.models.magis.magis import MAGIS
        from magi.bus.db.magis import open_magis_session

        with open_magis_session() as session:
            root = session.scalar(
                select(MAGIS).where(MAGIS.parent_id.is_(None)).order_by(MAGIS.id)
            )
            if root is not None and root.adam_id == magic_id:
                return os.environ.get("MAGI_ROOT_RUNTIME_URL", "http://magi:42069")
        return None

    def adam_url(self, magis_id: int, current_magic_id: int) -> tuple[int, str] | None:
        from sqlalchemy import select

        from magi.bus.models.magis.eva_runtime import EvaRuntime
        from magi.bus.models.magis.magis import MAGIS
        from magi.bus.db.magis import open_magis_session

        with open_magis_session() as session:
            magis = session.get(MAGIS, magis_id)
            if magis is None or magis.adam_id is None or magis.adam_id == current_magic_id:
                return None
            runtime = session.scalar(
                select(EvaRuntime).where(EvaRuntime.magic_id == magis.adam_id)
            )
            if (
                runtime
                and runtime.deployment_name
                and runtime.observed_state not in {"stopped", "deleted"}
            ):
                return magis.adam_id, f"http://{runtime.deployment_name}:42069"
            root = session.scalar(
                select(MAGIS).where(MAGIS.parent_id.is_(None)).order_by(MAGIS.id)
            )
            if root and root.adam_id == magis.adam_id:
                return magis.adam_id, os.environ.get("MAGI_ROOT_RUNTIME_URL", "http://magi:42069")
        return None

    def list_admin_accounts(self, magis_id: int) -> list[MagisAdminView]:
        return self.list_admins(magis_id)

    def control_setting_get(self, key: str) -> str | None:
        from magi.bus.models.local.control_plane import ControlSetting
        from magi.bus.db.magis import open_magis_session
        with open_magis_session() as session:
            row = session.get(ControlSetting, key)
            return row.value if row else None

    def control_setting_set(self, key: str, value: str) -> None:
        from magi.bus.models.local.control_plane import ControlSetting
        from magi.bus.db.magis import open_magis_session
        with open_magis_session() as session:
            row = session.get(ControlSetting, key)
            if row is None:
                session.add(ControlSetting(key=key, value=value))
            else:
                row.value = value
            session.commit()

    def control_setting_delete(self, key: str) -> None:
        from magi.bus.models.local.control_plane import ControlSetting
        from magi.bus.db.magis import open_magis_session
        with open_magis_session() as session:
            row = session.get(ControlSetting, key)
            if row is not None:
                session.delete(row)
                session.commit()

    def control_setting_list_prefix(self, prefix: str) -> dict[str, str]:
        from magi.bus.models.local.control_plane import ControlSetting
        from magi.bus.db.magis import open_magis_session
        from sqlalchemy import select

        with open_magis_session() as session:
            rows = session.scalars(
                select(ControlSetting).where(ControlSetting.key.startswith(prefix))
            ).all()
            return {row.key: row.value for row in rows}


class RuntimeConfigurationProjection:
    """Snapshot for a runtime container's direct-MAGIS view.

    Carries the inputs the control-plane orchestrator hands the bus when
    provisioning a fresh MAGIS database for an MAGI Pod.  Kept as a
    dataclass so :class:`MagisService.project_runtime_configuration`
    can be called from non-FastAPI code (the orchestrator service,
    tests, recovery scripts) without dragging in Pydantic or HTTP
    types.
    """

    magis_id: int
    magis_name: str
    magic_id: int
    magic_name: str | None = None
    personal_instruction: str | None = None
    provider: str | None = None
    api_key: str | None = None
    role_name: str = "EVA"
    role_instruction: str | None = None
    magis_instruction: str | None = None


def _project_runtime_configuration(spec: RuntimeConfigurationProjection, database_url: str) -> None:
    """Boot a runtime container's direct-MAGIS database.

    This is the control-plane equivalent of "create the row, seed the
    role, attach the membership".  Used by
    :class:`magi.orchestrator.kubernetes.KubernetesOrchestrator` when
    provisioning a fresh MAGI Pod so the container starts against a
    pre-populated PostgreSQL instead of an empty schema.  Bounded
    retry handles the case where the database Deployment hasn't
    finished becoming Ready yet.
    """
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session

    from magi.bus.models.magis.eva_runtime import EvaRuntime  # noqa: F401  # ensure table is registered
    from magi.bus.models.magis.magic import MAGIC
    from magi.bus.models.magis.magis import MAGIS
    from magi.bus.models.magis.magis_admin import MAGISAdmin  # noqa: F401
    from magi.bus.models.magis.magis_membership import (
        MAGISMembership,
        MAGISRole,
        ensure_default_roles,
    )

    engine = create_engine(database_url, pool_pre_ping=True, future=True)
    try:
        import time

        last_error: Exception | None = None
        for _attempt in range(20):
            try:
                MAGIS.metadata.create_all(
                    engine,
                    tables=[MAGIC.__table__, MAGIS.__table__, MAGISRole.__table__, MAGISMembership.__table__],
                )
                with Session(engine) as session:
                    society = session.get(MAGIS, spec.magis_id)
                    if society is None:
                        society = MAGIS(
                            id=spec.magis_id,
                            name=spec.magis_name,
                            instruction=spec.magis_instruction or "",
                        )
                        session.add(society)
                    else:
                        society.name = spec.magis_name
                        society.instruction = spec.magis_instruction or ""
                    session.flush()
                    ensure_default_roles(session, society.id)

                    magic = session.get(MAGIC, spec.magic_id)
                    if magic is None:
                        magic = MAGIC(id=spec.magic_id)
                        session.add(magic)
                    magic.name = spec.magic_name
                    magic.instruction = spec.personal_instruction
                    magic.provider = spec.provider
                    magic.api_key = spec.api_key

                    role = session.scalar(
                        select(MAGISRole).where(
                            MAGISRole.magis_id == society.id,
                            MAGISRole.name == spec.role_name,
                        )
                    )
                    if role is None:
                        role = MAGISRole(
                            magis_id=society.id,
                            name=spec.role_name,
                            instruction=spec.role_instruction or "",
                            is_reserved=spec.role_name in {"ADAM", "EVA"},
                        )
                        session.add(role)
                        session.flush()
                    else:
                        role.instruction = spec.role_instruction or ""

                    membership = session.scalar(
                        select(MAGISMembership).where(MAGISMembership.magic_id == magic.id)
                    )
                    if membership is None:
                        session.add(MAGISMembership(magis_id=society.id, magic_id=magic.id, role_id=role.id))
                    else:
                        membership.magis_id = society.id
                        membership.role_id = role.id

                    if role.name == "ADAM":
                        society.adam_id = magic.id

                    session.commit()
                return
            except Exception as exc:  # database Deployment may not be Ready yet
                last_error = exc
                time.sleep(1)
        raise RuntimeError(f"MAGIS database did not become ready: {last_error}")
    finally:
        engine.dispose()


# Bind to the service class so callers reach it via ``bus.magis``.
MagisService.project_runtime_configuration = staticmethod(_project_runtime_configuration)
MagisService.RuntimeConfigurationProjection = RuntimeConfigurationProjection
