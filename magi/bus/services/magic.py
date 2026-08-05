"""BUS service for public MAGIC runtime identity and configuration."""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any

from magi.bus.protocols.magis import (
    EvaRuntimeView,
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


# Phase 2 — lazy default dispatcher for callers that don't inject one
# via ``MagicService.__init__``.  Resolves on first use so tests that
# patch ``MAGI_BACKEND`` after import still pick the right backend.
_DEFAULT_DISPATCHER: Any | None = None


def _default_dispatcher() -> Any:
    """Return the process-wide default :class:`BackendDispatcherService`.

    Constructed on first call to honour any test-time ``MAGI_BACKEND``
    patches applied after import.  The dispatcher's body still calls the
    legacy K8s client for K8s deployments (Phase 4 will substitute).
    """
    global _DEFAULT_DISPATCHER
    if _DEFAULT_DISPATCHER is None:
        from magi.bus.services.runtime import BackendDispatcherService

        _DEFAULT_DISPATCHER = BackendDispatcherService()
    return _DEFAULT_DISPATCHER


def _runtime_view(runtime: Any) -> EvaRuntimeView:
    # Phase 2 — populate platform-neutral projection when the ORM row
    # carries it (Phase 4 will write these columns); fall back to
    # inferring from the legacy K8s fields for old rows.
    backend_kind = getattr(runtime, "backend_kind", None) or (
        "kubernetes" if getattr(runtime, "deployment_name", None) else None
    )
    backend_ref = getattr(runtime, "backend_ref", None) or getattr(
        runtime, "deployment_name", None
    )
    endpoint_url = getattr(runtime, "endpoint_url", None) or (
        f"http://{runtime.deployment_name}:42069"
        if getattr(runtime, "deployment_name", None)
        and str(runtime.observed_state or "") not in {"stopped", "deleted"}
        else None
    )
    return EvaRuntimeView(
        desired_state=str(runtime.desired_state),
        observed_state=str(runtime.observed_state),
        namespace=runtime.namespace,
        deployment_name=runtime.deployment_name,
        workspace_claim_name=runtime.workspace_claim_name,
        credential_secret_name=runtime.credential_secret_name,
        last_error=runtime.last_error,
        updated_at=_iso(runtime.updated_at),
        backend_kind=backend_kind,
        backend_ref=backend_ref,
        endpoint_url=endpoint_url,
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

    def __init__(
        self,
        state_dir: str | None = None,
        *,
        runtime_dispatcher: Any | None = None,
    ) -> None:
        self._state_dir = state_dir
        # Phase 2 — replaced the direct ``magi.orchestrator.client``
        # import with a BUS-injected dispatcher.  Default keeps the
        # legacy K8s behaviour bit-identical.
        self._runtime_dispatcher = runtime_dispatcher

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
        """Return the runtime MAGIC's LLM provider credentials.

        The 2026-08 refactor moved the credentials out of the shared
        ``magic`` row and into the per-MAGI ``runtime_settings.toml``
        file managed by :mod:`magi.bus.runtime_settings`.  The factory
        in :mod:`magi.providers.factory` is the sole consumer — it
        calls us on every LLM request, so the read must stay cheap and
        non-blocking.

        Falls back to the legacy ``magic.provider`` / ``magic.api_key``
        columns for rows created before the refactor, so older
        deployments keep working until their operators edit the
        per-MAGI file (or until a follow-up migration clears the
        columns).
        """
        from magi.bus.db.magis import open_magis_session
        from magi.bus.runtime_settings import load_runtime_settings

        with open_magis_session() as session:
            magic = self._runtime_magic(session)
            if magic is None:
                return None
        # Outside the PG session — the local file read is independent.
        rs = load_runtime_settings()
        if rs.has_credentials:
            return ProviderConfiguration(
                provider=str(rs.provider),
                api_key=str(rs.api_key),
                model=rs.model,
            )
        # Legacy fallback: pre-refactor rows still carry the values
        # inline on the magic row.
        if magic.provider and magic.api_key:
            return ProviderConfiguration(
                provider=str(magic.provider),
                api_key=str(magic.api_key),
                model=getattr(magic, "model", None),
            )
        return None

    def instruction_context(self) -> tuple[str, list[dict[str, str]]]:
        """Return only serialisable instruction facts for the active MAGIC."""
        from sqlalchemy import select

        from magi.bus.db.magis import open_magis_session
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
        from magi.bus.db.magis import open_magis_session
        from magi.bus.models.magis.magis_membership import can_route_a2a

        runtime_id = os.environ.get("MAGI_RUNTIME_ID")
        if not runtime_id or not runtime_id.isdigit():
            return False
        with open_magis_session() as session:
            return can_route_a2a(session, sender_magic_id, int(runtime_id))

    def get_runtime(self, magic_id: int) -> EvaRuntimeView | None:
        """Return the EVA runtime view for ``magic_id`` (or ``None``).

        Used by the WebUI runtime-proxy to decide whether a
        private-runtime upstream URL is reachable; the proxy treats
        ``None`` as "the runtime isn't tracked here, ask the
        root-runtime resolver".
        """
        from sqlalchemy import select

        from magi.bus.models.magis.eva_runtime import EvaRuntime
        from magi.bus.db.magis import open_magis_session

        with open_magis_session() as session:
            row = session.scalar(
                select(EvaRuntime).where(EvaRuntime.magic_id == magic_id)
            )
            return _runtime_view(row) if row is not None else None

    def list_all_magic(self) -> list[MagicView]:
        """Return every MAGIC row with its runtime + memberships populated.

        Used by the WebUI ``list_magic`` endpoint when the operator has
        no MAGIS scope (e.g. the singleton WebUI's bootstrap state).
        ``list_magic`` returns ``[]`` when both ``assigned_ids`` is
        empty and ``direct_ids`` is unset; ``list_all_magic`` is the
        explicit "show me everything" path.
        """
        from sqlalchemy import select

        from magi.bus.models.magis.eva_runtime import EvaRuntime
        from magi.bus.models.magis.magic import MAGIC
        from magi.bus.db.magis import open_magis_session

        with open_magis_session() as session:
            rows = session.scalars(select(MAGIC).order_by(MAGIC.id)).all()
            runtimes = {
                r.magic_id: r
                for r in session.scalars(
                    select(EvaRuntime).where(
                        EvaRuntime.magic_id.in_([m.id for m in rows])
                    )
                ).all()
            } if rows else {}
            return [
                _magic_view(session, magic, runtimes.get(magic.id))
                for magic in rows
            ]

    def get_instruction(self, magic_id: int) -> str | None:
        """Return the MAGIC's personal instruction text, or ``None`` when missing."""
        from magi.bus.models.magis.magic import MAGIC
        from magi.bus.db.magis import open_magis_session
        with open_magis_session() as session:
            magic = session.get(MAGIC, magic_id)
            return None if magic is None else str(magic.instruction or "")

    def list_available_magic(self) -> list[MagicView]:
        """Return MAGIC rows that are currently sign-in targets.

        A MAGIC is "available" when either it is the root MAGIS's
        ADAM (the static control-plane entry) or it has an
        ``EvaRuntime`` whose ``desired_state == "running"`` (the
        orchestrator has been asked to start a Deployment for it;
        Kubernetes does not synchronously write a later observed
        state back to the registry, so the desired state is the
        durable signal).

        The WebUI ``/available-magi`` endpoint renders this list as
        the login dropdown's primary set.
        """
        from sqlalchemy import select

        from magi.bus.models.magis.eva_runtime import EvaRuntime
        from magi.bus.models.magis.magic import MAGIC
        from magi.bus.models.magis.magis import MAGIS
        from magi.bus.db.magis import open_magis_session

        with open_magis_session() as session:
            rows = session.scalars(select(MAGIC).order_by(MAGIC.id)).all()
            runtimes = {
                row.magic_id: row
                for row in session.scalars(select(EvaRuntime)).all()
            }
            root = session.scalar(
                select(MAGIS).where(MAGIS.parent_id.is_(None)).order_by(MAGIS.id)
            )
            result: list[MagicView] = []
            for row in rows:
                runtime = runtimes.get(row.id)
                is_root_adam = root is not None and root.adam_id == row.id
                is_desired_running = (
                    runtime is not None and runtime.desired_state == "running"
                )
                if is_root_adam or is_desired_running:
                    result.append(_magic_view(session, row, runtime))
            return result

    # ------------------------------------------------------------------
    # CRUD operations exposed for ``magi.channels.api.magic``.
    # All methods accept primitive args (no FastAPI ``Request``) and return
    # frozen DTOs so callers never bind to ORM internals.
    # ------------------------------------------------------------------

    def list_magic(
        self,
        direct_ids: set[int] | None,
        assigned_ids: set[int],
    ) -> list[MagicView]:
        """Return visible MAGIs with their membership + runtime state.

        ``direct_ids`` are MAGIs bound to the current WebUI's direct
        MAGIS.  ``assigned_ids`` are MAGIs bound to *any* MAGIS; a
        MAGIC not in ``assigned_ids`` is treated as unassigned and
        visible regardless of scope.
        """
        from sqlalchemy import select

        from magi.bus.models.magis.eva_runtime import EvaRuntime
        from magi.bus.models.magis.magic import MAGIC
        from magi.bus.db.magis import open_magis_session

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
                        select(EvaRuntime).where(EvaRuntime.magic_id.in_([m.id for m in rows]))
                    ).all()
                }
            return [
                _magic_view(session, magic, runtimes.get(magic.id)) for magic in rows
            ]

    def get_magic(self, magic_id: int) -> MagicView | None:
        from sqlalchemy import select

        from magi.bus.models.magis.eva_runtime import EvaRuntime
        from magi.bus.models.magis.magic import MAGIC
        from magi.bus.db.magis import open_magis_session

        with open_magis_session() as session:
            magic = session.get(MAGIC, magic_id)
            if magic is None:
                return None
            runtime = session.scalar(
                select(EvaRuntime).where(EvaRuntime.magic_id == magic.id)
            )
            return _magic_view(session, magic, runtime)

    def create_magic(
        self,
        name: str | None,
        magis_id: int,
        role_id: int | None = None,
    ) -> MagicView:
        """Create a new MAGI row and bind it to ``magis_id`` in one transaction.

        The bootstrap seed reserves ``id = 0`` for ``EVA-000`` and the
        root ``Genesis`` MAGIS.  New MAGIs take ``id = max(id) + 1`` via
        the application-layer allocator in
        :func:`magi.bus.db.engine._next_id`, so SQLite's ROWID and
        Postgres' SERIAL sequence never collide with the seed row.

        A direct MAGIS membership is created in the same transaction
        (operator picks the MAGIS + role in the WebUI create form).
        When ``role_id`` is ``None`` we resolve the target MAGIS's
        reserved ``EVA`` role — the natural default for a worker
        archetype.  Raises ``ValueError`` on name collision, missing
        MAGIS, missing role, ADAM already taken, or duplicate direct
        membership.

        Provider / API key are no longer stored on this row at creation
        time — the new MAGI has no local settings yet because it
        hasn't started.  The WebUI exposes a runtime-only edit
        endpoint at ``PATCH /api/magic/self/provider`` once the
        runtime is up.  ``provider`` and ``api_key`` columns stay
        ``None`` on the new row; the legacy read path in
        :meth:`provider_configuration` falls back to them only for
        rows created before this refactor.
        """
        from magi.bus.db.engine import _next_id
        from magi.bus.models.magis.magic import MAGIC
        from magi.bus.models.magis.magis import MAGIS
        from magi.bus.models.magis.magis_membership import (
            MAGISMembership,
            MAGISRole,
            ensure_default_roles,
        )
        from magi.bus.db.magis import open_magis_session

        with open_magis_session() as session:
            magis = session.get(MAGIS, magis_id)
            if magis is None:
                raise ValueError(f"MAGIS {magis_id} not found")

            # Name uniqueness — case-insensitive comparison would be a
            # follow-up; right now we trust the unique index.
            if name is not None:
                existing = session.scalar(
                    __import__("sqlalchemy").select(MAGIC.id).where(MAGIC.name == name)
                )
                if existing is not None:
                    raise ValueError(f"MAGI name {name!r} already exists")

            new_id = _next_id(session, MAGIC)
            magic = MAGIC(id=new_id, name=name)
            session.add(magic)
            session.flush()  # populate magic.id

            # Resolve the role.  ``role_id`` is optional; when omitted
            # we default to the target MAGIS's reserved EVA role so
            # operator-typed "EVA-001 under Genesis" Just Works.
            roles = ensure_default_roles(session, magis.id)
            if role_id is None:
                role = roles.get("EVA")
                if role is None:
                    raise ValueError(
                        f"MAGIS {magis_id} has no reserved EVA role"
                    )
            else:
                role = session.get(MAGISRole, role_id)
                if role is None or role.magis_id != magis.id:
                    raise ValueError(
                        f"role {role_id} is not in MAGIS {magis_id}"
                    )

            # ADAM role uniqueness: the target MAGIS may already have
            # an ADAM assigned; refuse to assign a second one.
            if role.name == "ADAM":
                if magis.adam_id is not None and int(magis.adam_id) != int(magic.id):
                    raise ValueError("this MAGIS already has an ADAM")
                magis.adam_id = magic.id
            elif magis.adam_id == magic.id:
                # Demoting the existing ADAM to a non-ADAM role — clear
                # the parent MAGIS pointer so it stays consistent.
                magis.adam_id = None

            session.add(MAGISMembership(
                magis_id=magis.id,
                magic_id=magic.id,
                role_id=role.id,
            ))
            session.commit()
            session.refresh(magic)
            return _magic_view(session, magic)

    def update_magic(
        self,
        magic_id: int,
        *,
        name: str | None | Any = _FIELD_UNSET,
    ) -> MagicView:
        """Partial update.  Each kwarg defaults to ``_FIELD_UNSET``; pass
        ``None`` explicitly to clear a column.  Raises ``LookupError`` if
        the MAGIC is missing.

        Provider / API key / model editing no longer flows through this
        entry — the operator edits those on the target MAGI's runtime
        via ``PATCH /api/magic/self/provider``.  The ``magic.provider``
        / ``magic.api_key`` columns stay ``None`` for rows created
        after the creation-flow refactor; the legacy read path in
        :meth:`provider_configuration` falls back to them only for
        pre-refactor rows.
        """
        from sqlalchemy import select

        from magi.bus.models.magis.eva_runtime import EvaRuntime
        from magi.bus.models.magis.magic import MAGIC
        from magi.bus.db.magis import open_magis_session

        with open_magis_session() as session:
            magic = session.get(MAGIC, magic_id)
            if magic is None:
                raise LookupError(f"magic {magic_id} not found")
            if name is not _FIELD_UNSET:
                magic.name = name
            session.commit()
            runtime = session.scalar(
                select(EvaRuntime).where(EvaRuntime.magic_id == magic.id)
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

        from magi.bus.models.magis.eva_runtime import EvaRuntime
        from magi.bus.models.magis.magic import MAGIC
        from magi.bus.db.magis import open_magis_session

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
                select(EvaRuntime).where(EvaRuntime.magic_id == magic.id)
            )
            if runtime is not None and runtime.deployment_name:
                # Phase 2 — replaced direct ``magi.orchestrator.client``
                # import with the BUS dispatcher.  The dispatcher
                # internally still hits the legacy K8s client so the
                # K8s Profile is bit-identical; Phase 4 substitutes the
                # body with a real BUS command queue.
                from magi.bus.protocols.lifecycle import RuntimeSpec

                spec = RuntimeSpec(magic_id=magic.id, name=magic.name)
                dispatcher = self._runtime_dispatcher or _default_dispatcher()
                dispatcher.delete(spec)
            session.delete(magic)
            session.commit()
            return True

    def set_instruction(self, magic_id: int, instruction: str) -> MagicView:
        """Replace the MAGIC's personal instruction block.

        ``instruction`` is stored verbatim; length validation is the
        caller's responsibility (the WebUI caps it at 12 000 chars).
        """
        from sqlalchemy import select

        from magi.bus.models.magis.eva_runtime import EvaRuntime
        from magi.bus.models.magis.magic import MAGIC
        from magi.bus.db.magis import open_magis_session

        with open_magis_session() as session:
            magic = session.get(MAGIC, magic_id)
            if magic is None:
                raise LookupError(f"magic {magic_id} not found")
            magic.instruction = instruction
            session.commit()
            runtime = session.scalar(
                select(EvaRuntime).where(EvaRuntime.magic_id == magic.id)
            )
            return _magic_view(session, magic, runtime)

    def list_memberships(self, magic_id: int) -> list[MembershipBrief]:
        """Return the MAGIC's direct MAGIS memberships with role names."""
        from magi.bus.db.magis import open_magis_session

        with open_magis_session() as session:
            return _memberships_for(session, magic_id)

    def set_runtime(
        self,
        magic_id: int,
        desired_state: str,
        *,
        lifecycle_action: str | None = None,
    ) -> EvaRuntimeView:
        """Persist ``desired_state`` on the MAGIC's ``EvaRuntime`` row.

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

        from magi.bus.models.magis.eva_runtime import EvaRuntime
        from magi.bus.models.magis.magic import MAGIC
        from magi.bus.db.magis import open_magis_session

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
                select(EvaRuntime).where(EvaRuntime.magic_id == magic.id)
            )
            if runtime is None:
                runtime = EvaRuntime(magic_id=magic.id)
                session.add(runtime)
                session.flush()

            runtime.desired_state = desired_state

            if lifecycle_action in {"start", "stop"}:
                if lifecycle_action == "start":
                    if _direct_magis_binding(session, magic.id) is None:
                        raise ValueError(
                            "assign this MAGI to a MAGIS before starting it"
                        )
                    # 2026-08 refactor: provider / API key live in
                    # the per-MAGI runtime settings file, not on the
                    # shared magic row.  ``provider_configuration``
                    # already handles the legacy fallback for
                    # pre-refactor rows, so we delegate the check
                    # there for consistency with the factory read
                    # path.  Open the local file outside the PG
                    # session so we don't hold a write lock while
                    # doing it.
                    from magi.bus.runtime_settings import load_runtime_settings

                    rs = load_runtime_settings()
                    has_legacy = bool(magic.provider and magic.api_key)
                    if not rs.has_credentials and not has_legacy:
                        raise ValueError(
                            "configure provider and API key before starting this MAGI"
                        )
                # Phase 2 — replaced direct ``magi.orchestrator.client``
                # import with the BUS dispatcher.  The dispatcher
                # internally still hits the legacy K8s client so the
                # K8s Profile is bit-identical; Phase 4 substitutes the
                # body with a real BUS command queue.
                from magi.bus.protocols.lifecycle import RuntimeSpec

                binding = _direct_magis_binding(session, magic.id)
                spec = RuntimeSpec(
                    magic_id=magic.id,
                    name=magic.name,
                    magis_id=(binding[2].id if binding is not None else None),
                    magis_name=(binding[2].name if binding is not None else None),
                )
                dispatcher = self._runtime_dispatcher or _default_dispatcher()
                try:
                    if lifecycle_action == "start":
                        result = dispatcher.start(spec)
                    else:
                        result = dispatcher.stop(spec)
                except Exception as exc:
                    runtime.observed_state = "failed"
                    runtime.last_error = str(exc)
                    session.commit()
                    raise
                runtime.observed_state = result.observed_state
                if result.kubernetes_detail is not None:
                    # Legacy ORM fields — populated only by the K8s
                    # backend; the Local backend leaves them alone.
                    runtime.namespace = result.kubernetes_detail.namespace
                    runtime.deployment_name = result.kubernetes_detail.deployment_name
                    runtime.workspace_claim_name = result.kubernetes_detail.workspace_claim_name
                    runtime.credential_secret_name = result.kubernetes_detail.credential_secret_name
                else:
                    runtime.namespace = None
                    runtime.deployment_name = None
                    runtime.workspace_claim_name = None
                    runtime.credential_secret_name = None
                runtime.last_error = None

            session.commit()
            return _runtime_view(runtime)

    # ------------------------------------------------------------------
    # Runtime identity + visibility helpers for the WebUI channel.
    # ------------------------------------------------------------------

    def current_runtime_magic_id(self) -> int | None:
        """Return the ``MAGI`` id served by this WebUI, or ``None``.

        Reads ``MAGI_RUNTIME_ID`` first; falls back to the root
        MAGIS's ``adam_id``.  Mirrors :meth:`MagisService.current_runtime_magic_id`
        — kept here so the API can resolve WebUI-bound MAGIs without
        reaching into the MAGIS table on its own.
        """
        from magi.bus.db.magis import open_magis_session
        from magi.bus.models.magis.magis import MAGIS
        from sqlalchemy import select

        runtime_id = os.environ.get("MAGI_RUNTIME_ID")
        if runtime_id and runtime_id.isdigit():
            return int(runtime_id)
        with open_magis_session() as session:
            root = session.scalar(
                select(MAGIS).where(MAGIS.parent_id.is_(None)).order_by(MAGIS.id)
            )
            return int(root.adam_id) if root and root.adam_id else None

    def ensure_runtime(self, magic_id: int) -> EvaRuntimeView:
        """Return the EvaRuntimeView for ``magic_id``, creating one if missing."""
        from sqlalchemy import select

        from magi.bus.models.magis.eva_runtime import EvaRuntime
        from magi.bus.models.magis.magic import MAGIC
        from magi.bus.db.magis import open_magis_session

        with open_magis_session() as session:
            if session.get(MAGIC, magic_id) is None:
                raise LookupError(f"magic {magic_id} not found")
            runtime = session.scalar(
                select(EvaRuntime).where(EvaRuntime.magic_id == magic_id)
            )
            if runtime is None:
                runtime = EvaRuntime(magic_id=magic_id)
                session.add(runtime)
                session.commit()
                session.refresh(runtime)
            return _runtime_view(runtime)