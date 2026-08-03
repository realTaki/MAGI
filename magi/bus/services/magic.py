"""BUS service for public MAGIC runtime identity and configuration."""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any

from magi.bus.contracts.magis import (
    EveRuntimeView,
    MagicView,
    MembershipBrief,
    ProviderConfiguration,
)


# Sentinel used by partial-update methods to distinguish "caller omitted
# this field" from "caller explicitly set this field to None".  Service
# kwargs default to this so the API layer can forward Pydantic
# ``model_fields_set`` directly.
_FIELD_UNSET: Any = object()


def _iso(value: datetime | None) -> str:
    return value.isoformat() if value is not None else ""


def _runtime_view(runtime: Any) -> EveRuntimeView:
    return EveRuntimeView(
        desired_state=str(runtime.desired_state),
        observed_state=str(runtime.observed_state),
        namespace=runtime.namespace,
        deployment_name=runtime.deployment_name,
        workspace_claim_name=runtime.workspace_claim_name,
        credential_secret_name=runtime.credential_secret_name,
        last_error=runtime.last_error,
        updated_at=_iso(runtime.updated_at),
    )


def _memberships_for(session: Any, magic_id: int) -> list[MembershipBrief]:
    from sqlalchemy import select

    from magi.bus.models.magis.magis import MAGIS
    from magi.bus.models.magis.magis_membership import MAGISMembership, MAGISRole

    rows = session.execute(
        select(MAGISMembership, MAGISRole, MAGIS)
        .join(MAGISRole, MAGISRole.id == MAGISMembership.role_id)
        .join(MAGIS, MAGIS.id == MAGISMembership.magis_id)
        .where(MAGISMembership.magic_id == magic_id)
        .order_by(MAGISMembership.id)
    ).all()
    return [
        MembershipBrief(
            magis_id=int(m.magis_id),
            magis_name=str(s.name),
            role_id=int(r.id),
            role_name=str(r.name),
        )
        for m, r, s in rows
    ]


def _magic_view(session: Any, magic: Any, runtime: Any | None = None) -> MagicView:
    return MagicView(
        id=int(magic.id),
        name=magic.name,
        provider=magic.provider,
        api_key_set=bool(magic.api_key),
        api_key_last4=(magic.api_key[-4:] if magic.api_key else None),
        memberships=_memberships_for(session, int(magic.id)),
        runtime=_runtime_view(runtime) if runtime is not None else None,
        created_at=_iso(magic.created_at),
        updated_at=_iso(magic.updated_at),
    )


def _direct_magis_binding(session: Any, magic_id: int):
    """Return ``(membership, role, magis)`` for the MAGIC's one direct binding.

    Mirrors ``api/magic.py._direct_magis_binding`` so callers can decide
    whether the MAGIC is assigned and which role it holds.
    """
    from sqlalchemy import select

    from magi.bus.models.magis.magis import MAGIS
    from magi.bus.models.magis.magis_membership import MAGISMembership, MAGISRole

    return session.execute(
        select(MAGISMembership, MAGISRole, MAGIS)
        .join(MAGISRole, MAGISRole.id == MAGISMembership.role_id)
        .join(MAGIS, MAGIS.id == MAGISMembership.magis_id)
        .where(MAGISMembership.magic_id == magic_id)
        .order_by(MAGISMembership.id)
    ).first()


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

    # ------------------------------------------------------------------
    # CRUD operations exposed for ``magi.channels.webui.api.magic``.
    # All methods accept primitive args (no FastAPI ``Request``) and return
    # frozen DTOs so callers never bind to ORM internals.
    # ------------------------------------------------------------------

    def list_magic(
        self,
        served: int | None,
        direct_ids: set[int] | None,
        assigned_ids: set[int],
    ) -> list[MagicView]:
        """Return visible MAGIs with their membership + runtime state.

        ``served`` is the current WebUI's direct MAGIS id (or ``None`` if
        no MAGIS is served).  ``direct_ids`` are MAGIs bound to that
        served MAGIS.  ``assigned_ids`` are MAGIs bound to *any* MAGIS;
        a MAGIC not in ``assigned_ids`` is treated as unassigned and
        visible regardless of scope.
        """
        from sqlalchemy import select

        from magi.bus.models.magis.eve_runtime import EveRuntime
        from magi.bus.models.magis.magic import MAGIC
        from magi.db.magis import open_magis_session

        direct = set(direct_ids) if direct_ids is not None else set()
        with open_magis_session() as session:
            if assigned_ids:
                rows = session.scalars(
                    select(MAGIC)
                    .where((MAGIC.id.in_(direct)) | (~MAGIC.id.in_(assigned_ids)))
                    .order_by(MAGIC.id)
                ).all()
            else:
                rows = []
            runtimes: dict[int, Any] = {}
            if rows:
                runtimes = {
                    r.magic_id: r
                    for r in session.scalars(
                        select(EveRuntime).where(EveRuntime.magic_id.in_([m.id for m in rows]))
                    ).all()
                }
            return [
                _magic_view(session, magic, runtimes.get(magic.id)) for magic in rows
            ]

    def get_magic(self, magic_id: int) -> MagicView | None:
        from sqlalchemy import select

        from magi.bus.models.magis.eve_runtime import EveRuntime
        from magi.bus.models.magis.magic import MAGIC
        from magi.db.magis import open_magis_session

        with open_magis_session() as session:
            magic = session.get(MAGIC, magic_id)
            if magic is None:
                return None
            runtime = session.scalar(
                select(EveRuntime).where(EveRuntime.magic_id == magic.id)
            )
            return _magic_view(session, magic, runtime)

    def create_magic(
        self,
        name: str | None,
        provider: str | None,
        api_key: str | None,
    ) -> MagicView:
        from magi.bus.models.magis.magic import MAGIC
        from magi.db.magis import open_magis_session

        with open_magis_session() as session:
            magic = MAGIC(name=name, provider=provider, api_key=api_key)
            session.add(magic)
            session.commit()
            session.refresh(magic)
            return _magic_view(session, magic)

    def update_magic(
        self,
        magic_id: int,
        *,
        name: str | None | Any = _FIELD_UNSET,
        provider: str | None | Any = _FIELD_UNSET,
        api_key: str | None | Any = _FIELD_UNSET,
    ) -> MagicView:
        """Partial update.  Each kwarg defaults to ``_FIELD_UNSET``; pass
        ``None`` explicitly to clear a column.  Raises ``LookupError`` if
        the MAGIC is missing.
        """
        from sqlalchemy import select

        from magi.bus.models.magis.eve_runtime import EveRuntime
        from magi.bus.models.magis.magic import MAGIC
        from magi.db.magis import open_magis_session

        with open_magis_session() as session:
            magic = session.get(MAGIC, magic_id)
            if magic is None:
                raise LookupError(f"magic {magic_id} not found")
            if name is not _FIELD_UNSET:
                magic.name = name
            if provider is not _FIELD_UNSET:
                magic.provider = provider
            if api_key is not _FIELD_UNSET:
                magic.api_key = api_key or None
            session.commit()
            runtime = session.scalar(
                select(EveRuntime).where(EveRuntime.magic_id == magic.id)
            )
            return _magic_view(session, magic, runtime)

    def delete_magic(self, magic_id: int) -> bool:
        """Delete a MAGIC plus its orchestrator-side Deployment if any.

        Returns ``True`` when the row existed and was deleted, ``False``
        when no matching MAGIC exists.  Raises ``PermissionError`` if the
        caller tries to delete the MAGI currently serving this runtime,
        and propagates ``OrchestratorUnavailable`` from the control
        plane when the orchestrator call fails.
        """
        from sqlalchemy import select

        from magi.bus.models.magis.eve_runtime import EveRuntime
        from magi.bus.models.magis.magic import MAGIC
        from magi.db.magis import open_magis_session

        with open_magis_session() as session:
            magic = session.get(MAGIC, magic_id)
            if magic is None:
                return False
            runtime_magic = self._runtime_magic(session)
            if runtime_magic is not None and runtime_magic.id == magic.id:
                raise PermissionError(
                    "cannot delete the MAGI currently serving this session"
                )
            runtime = session.scalar(
                select(EveRuntime).where(EveRuntime.magic_id == magic.id)
            )
            if runtime is not None and runtime.deployment_name:
                from magi.orchestrator.client import request_lifecycle
                from magi.orchestrator.contracts import EveSpec

                request_lifecycle("delete", EveSpec(magic_id=magic.id, name=magic.name))
            session.delete(magic)
            session.commit()
            return True

    def set_instruction(self, magic_id: int, instruction: str) -> MagicView:
        """Replace the MAGIC's personal instruction block.

        ``instruction`` is stored verbatim; length validation is the
        caller's responsibility (the WebUI caps it at 12 000 chars).
        """
        from sqlalchemy import select

        from magi.bus.models.magis.eve_runtime import EveRuntime
        from magi.bus.models.magis.magic import MAGIC
        from magi.db.magis import open_magis_session

        with open_magis_session() as session:
            magic = session.get(MAGIC, magic_id)
            if magic is None:
                raise LookupError(f"magic {magic_id} not found")
            magic.instruction = instruction
            session.commit()
            runtime = session.scalar(
                select(EveRuntime).where(EveRuntime.magic_id == magic.id)
            )
            return _magic_view(session, magic, runtime)

    def list_memberships(self, magic_id: int) -> list[MembershipBrief]:
        """Return the MAGIC's direct MAGIS memberships with role names."""
        from magi.db.magis import open_magis_session

        with open_magis_session() as session:
            return _memberships_for(session, magic_id)

    def set_runtime(
        self,
        magic_id: int,
        desired_state: str,
        *,
        lifecycle_action: str | None = None,
    ) -> EveRuntimeView:
        """Persist ``desired_state`` on the MAGIC's ``EveRuntime`` row.

        When ``lifecycle_action`` is ``"start"`` or ``"stop"``, the
        service also calls the orchestrator control plane to apply the
        change to the Kubernetes Deployment and updates ``observed_state``
        + resource names from the result.  When ``lifecycle_action`` is
        ``None``, only the desired state is persisted.  Raises
        ``LookupError`` when the MAGIC is missing, ``PermissionError`` if
        the caller targets the runtime's own MAGIC, and propagates
        ``OrchestratorUnavailable`` from the control plane.
        """
        from sqlalchemy import select

        from magi.bus.models.magis.eve_runtime import EveRuntime
        from magi.bus.models.magis.magic import MAGIC
        from magi.db.magis import open_magis_session

        if desired_state not in {"running", "stopped", "draft", "deleted"}:
            raise ValueError(f"unsupported desired_state: {desired_state!r}")
        with open_magis_session() as session:
            magic = session.get(MAGIC, magic_id)
            if magic is None:
                raise LookupError(f"magic {magic_id} not found")
            runtime_magic = self._runtime_magic(session)
            if runtime_magic is not None and runtime_magic.id == magic.id:
                raise PermissionError(
                    "cannot stop or restart the MAGI currently serving this session"
                )
            runtime = session.scalar(
                select(EveRuntime).where(EveRuntime.magic_id == magic.id)
            )
            if runtime is None:
                runtime = EveRuntime(magic_id=magic.id)
                session.add(runtime)
                session.flush()

            runtime.desired_state = desired_state

            if lifecycle_action in {"start", "stop"}:
                if lifecycle_action == "start":
                    if _direct_magis_binding(session, magic.id) is None:
                        raise ValueError(
                            "assign this MAGI to a MAGIS before starting it"
                        )
                    if not magic.provider or not magic.api_key:
                        raise ValueError(
                            "configure provider and API key before starting this MAGI"
                        )
                from magi.orchestrator.client import request_lifecycle
                from magi.orchestrator.contracts import (
                    EveSpec,
                    MagisBinding,
                    MagisRuntimeConfiguration,
                )

                binding = _direct_magis_binding(session, magic.id)
                magis = (
                    MagisBinding(id=binding[2].id, name=binding[2].name)
                    if binding is not None
                    else None
                )
                configuration = (
                    MagisRuntimeConfiguration(
                        magis_instruction=binding[2].instruction,
                        role_name=binding[1].name,
                        role_instruction=binding[1].instruction,
                        magic_name=magic.name,
                        personal_instruction=magic.instruction,
                        provider=magic.provider,
                        api_key=magic.api_key,
                    )
                    if binding is not None
                    else None
                )
                spec = EveSpec(
                    magic_id=magic.id, name=magic.name,
                    magis=magis, configuration=configuration,
                )
                try:
                    result = request_lifecycle(lifecycle_action, spec)
                except Exception as exc:
                    runtime.observed_state = "failed"
                    runtime.last_error = str(exc)
                    session.commit()
                    raise
                runtime.observed_state = result.observed_state
                runtime.namespace = result.namespace
                runtime.deployment_name = result.deployment_name
                runtime.workspace_claim_name = result.workspace_claim_name
                runtime.credential_secret_name = result.credential_secret_name
                runtime.last_error = None

            session.commit()
            return _runtime_view(runtime)