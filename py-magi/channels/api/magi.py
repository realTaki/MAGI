"""Per-MAGI identity, runtime-status, and self-instruction APIs.

A MAGI is the identity of a :class:`MagisMembership` row (no separate
``magic`` table anymore — the term was retired when ``magic`` collapsed
into ``magis_memberships``).  The control-plane runtime record adds the
operator-facing name and lifecycle intent; each running MAGI keeps
its personal instruction in its own ``settings_book``.

The module intentionally exposes two routers.  Management routes are mounted
only by the control WebUI, while the self-instruction route is mounted only by
private runtimes.  That keeps a control process from reading or writing a
different MAGI's node-local settings.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field

from magi.old_bus import Bus
from magi.old_bus.firmwares.books.magis.runtimeBook import RuntimeDesiredState
from magi.channels.api.auth_gates import admin_gate
from magi.channels.api.dependencies import BusDep
from magi.channels.api.errors import MagiHTTPException
from magi.channels.api.proxy_auth import build_proxy_headers
from magi.channels.api.runtime_http import PROXY_TIMEOUT

logger = logging.getLogger("magi.api.magi")

router = APIRouter(tags=["magi"])
self_router = APIRouter(tags=["magi"])
AdminGate = Annotated[str, Depends(admin_gate)]


class MembershipBrief(BaseModel):
    magis_id: int
    magis_name: str
    role_id: int
    role_name: str


class RuntimeOut(BaseModel):
    desired_state: str
    observed_state: str
    namespace: str | None = None
    deployment_name: str | None = None
    workspace_claim_name: str | None = None
    credential_secret_name: str | None = None
    last_error: str | None = None
    updated_at: datetime | None = None


class MagiOut(BaseModel):
    id: int
    name: str | None = None
    provider: str | None = None
    api_key_set: bool = False
    api_key_last4: str | None = None
    memberships: list[MembershipBrief]
    runtime: RuntimeOut | None = None
    created_at: datetime
    updated_at: datetime


class MagiCreate(BaseModel):
    name: str | None = Field(default=None, max_length=100)
    magis_id: int = Field(ge=1)
    role_id: int | None = Field(default=None, ge=1)


class MagiUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class InstructionPayload(BaseModel):
    instruction: str = Field(max_length=12000)


class InstructionOut(BaseModel):
    magi_id: int
    instruction: str


def _runtime_magi_id() -> int:
    value = os.environ.get("MAGI_RUNTIME_ID")
    if not value or not value.isdigit():
        raise MagiHTTPException(503, "runtime.identity_missing", "MAGI runtime identity is missing")
    return int(value)


def _served_direct_magis_id(bus: Bus) -> int | None:
    raw = os.environ.get("MAGI_RUNTIME_ID")
    if not raw or not raw.isdigit() or bus.memberships_book is None:
        return None
    membership = bus.memberships_book.get(int(raw))
    return membership.magis_id if membership is not None else None


def _require_visible(bus: Bus, magi_id: int) -> None:
    membership = bus.memberships_book.get(magi_id) if bus.memberships_book else None
    if membership is None:
        raise MagiHTTPException(404, "not_found.magi", "MAGI not found")
    served = _served_direct_magis_id(bus)
    if served is not None and membership.magis_id != served:
        raise MagiHTTPException(
            403, "forbidden.magi_management_scope", "MAGI is outside the current direct MAGIS"
        )


def _runtime_out(runtime) -> RuntimeOut | None:
    if runtime is None:
        return None
    if runtime.backend_kind == "unprovisioned":
        return RuntimeOut(
            desired_state="draft",
            observed_state="draft",
            deployment_name=runtime.backend_ref,
            updated_at=runtime.updated_at,
        )
    desired = getattr(runtime.desired_state, "value", runtime.desired_state)
    observed = getattr(runtime.observed_state, "value", runtime.observed_state)
    desired = {"started": "running", "stopped": "stopped"}.get(str(desired), str(desired))
    observed = {
        "starting": "provisioning",
        "started": "running",
        "stopping": "stopped",
        "stopped": "stopped",
        "crashed": "failed",
    }.get(str(observed), str(observed))
    return RuntimeOut(
        desired_state=desired,
        observed_state=observed,
        deployment_name=runtime.backend_ref,
        updated_at=runtime.updated_at,
    )


def _magi_out(bus: Bus, membership) -> MagiOut:
    """Build a sync ``MagiOut`` without provider info.

    Provider settings live on the runtime's private ``settings_book``,
    which the control plane cannot read directly; callers that need
    them populated must use :func:`_magi_out_with_provider` instead.
    """
    magis = bus.magis_book.get(membership.magis_id) if bus.magis_book else None
    role = bus.roles_book.get(membership.role_id) if bus.roles_book else None
    runtime = (
        bus.runtime_state_book.get_by_runtime_id(runtime_id=membership.id) if bus.runtime_state_book else None
    )
    return MagiOut(
        id=membership.id,
        name=runtime.backend_ref if runtime else None,
        memberships=[
            MembershipBrief(
                magis_id=membership.magis_id,
                magis_name=magis.name if magis else f"MAGIS {membership.magis_id}",
                role_id=membership.role_id,
                role_name=role.name if role else "",
            )
        ],
        runtime=_runtime_out(runtime),
        created_at=membership.created_at,
        updated_at=membership.updated_at,
    )


async def _fetch_runtime_provider(
    bus: Bus,
    runtime_base_url: str,
    *,
    magi_id: int,
) -> tuple[str | None, bool, str | None] | None:
    """HMAC-signed ``GET /api/magi/self/provider`` against one runtime.

    Returns ``(provider, api_key_set, api_key_last4)`` on success, or
    ``None`` when the runtime is unreachable / refused the call.  The
    control plane needs this because provider settings live on each
    runtime's private ``settings_book`` (per-node SQLite), not on the
    MAGIS shared store.

    Errors are intentionally swallowed: a missing / dead runtime must
    not break the surrounding ``list_magi`` response — the row will
    show ``provider=null`` / ``api_key_set=false`` and the operator can
    fix the runtime out-of-band.
    """
    headers = build_proxy_headers(
        bus=bus,
        method="GET",
        path_and_query="/api/magi/self/provider",
        target_id=magi_id,
        operator_id=0,
        operator_name="webui-magi-list",
        tgid=None,
        magis_admin_id=1,
        admin=True,
        assigned=True,
        two_factor=False,
    )
    try:
        async with httpx.AsyncClient(timeout=PROXY_TIMEOUT) as client:
            response = await client.get(runtime_base_url + "/api/magi/self/provider", headers=headers)
    except (httpx.HTTPError, OSError) as exc:
        logger.debug(
            "magi/self/provider fetch failed for magi_id=%s (%s): %s",
            magi_id, runtime_base_url, exc,
        )
        return None
    if response.is_error:
        return None
    try:
        data = response.json()
    except ValueError:
        return None
    return (
        data.get("provider"),
        bool(data.get("api_key_set")),
        data.get("api_key_last4"),
    )


async def _magi_out_with_provider(bus: Bus, membership) -> MagiOut:
    """Async variant of :func:`_magi_out` that also fills provider fields.

    Provider info is fetched from the live runtime via HMAC-signed
    proxy when ``observed_state in {starting, started}``; otherwise the
    fields fall back to ``None`` / ``False`` so the row reflects the
    current state truthfully.
    """
    out = _magi_out(bus, membership)
    runtime = (
        bus.runtime_state_book.get_by_runtime_id(runtime_id=membership.id)
        if bus.runtime_state_book
        else None
    )
    if runtime is not None and runtime.base_url and runtime.observed_state in {"starting", "started"}:
        fetched = await _fetch_runtime_provider(bus, runtime.base_url, magi_id=membership.id)
        if fetched is not None:
            out.provider, out.api_key_set, out.api_key_last4 = fetched
    return out


def _membership_or_404(bus: Bus, magi_id: int):
    membership = bus.memberships_book.get(magi_id) if bus.memberships_book else None
    if membership is None:
        raise MagiHTTPException(404, "not_found.magi", "MAGI not found")
    return membership


def _default_eva_role(bus: Bus, magis_id: int):
    roles = bus.roles_book.list_for_magis(magis_id=magis_id) if bus.roles_book else []
    return next((role for role in roles if role.name == "EVA"), None)


@router.get("/magi", response_model=list[MagiOut])
async def list_magi(_admin: AdminGate, bus: BusDep) -> list[MagiOut]:
    memberships = []
    if bus.magis_book and bus.memberships_book:
        served = _served_direct_magis_id(bus)
        for magis in bus.magis_book.list_all():
            if served is None or magis.id == served:
                memberships.extend(bus.memberships_book.list_for_magis(magis_id=magis.id))
    # Provider fields require an async fetch per MAGI; gather them
    # concurrently so the list call stays O(1) in wall-clock time.
    return list(await asyncio.gather(*[_magi_out_with_provider(bus, m) for m in memberships]))


@router.post("/magi", response_model=MagiOut, status_code=201)
def create_magi(payload: MagiCreate, _admin: AdminGate, bus: BusDep) -> MagiOut:
    if bus.magis_book is None or bus.memberships_book is None or bus.roles_book is None:
        raise MagiHTTPException(503, "magis.unavailable", "MAGIS services are unavailable")
    if bus.magis_book.get(payload.magis_id) is None:
        raise MagiHTTPException(404, "not_found.magis", "MAGIS not found")
    role = (
        bus.roles_book.get(payload.role_id)
        if payload.role_id
        else _default_eva_role(bus, payload.magis_id)
    )
    if role is None or role.magis_id != payload.magis_id:
        raise MagiHTTPException(
            400, "validation.magi_role", "role must belong to the selected MAGIS"
        )
    from magi.old_bus.firmwares.books.magis.membershipBook import MagisMembership

    membership_id = bus.memberships_book.add(
        MagisMembership(magis_id=payload.magis_id, role_id=role.id)
    )
    membership = bus.memberships_book.get(membership_id)
    if membership is None:
        raise RuntimeError(f"membership row {membership_id} disappeared after insert")
    # The identity is valid immediately.  A runtime is provisioned separately
    # by the node lifecycle; the control row keeps the display label while
    # that provisioning is pending.  Creating it for every identity also
    # makes later rename/delete semantics uniform.
    if bus.runtime_state_book:
        bus.runtime_state_book.upsert(
            runtime_id=membership.id,
            backend_kind="unprovisioned",
            backend_ref=(payload.name or f"EVA-{membership.id:03d}").strip()
            or f"EVA-{membership.id:03d}",
            workspace_dir="",
            log_dir="",
            audit_log_path="",
            port=None,
            base_url=None,
        )
    return _magi_out(bus, membership)


@router.get("/magi/{magi_id}", response_model=MagiOut)
async def get_magi(magi_id: int, _admin: AdminGate, bus: BusDep) -> MagiOut:
    _require_visible(bus, magi_id)
    return await _magi_out_with_provider(bus, _membership_or_404(bus, magi_id))


@router.patch("/magi/{magi_id}", response_model=MagiOut)
async def update_magi(magi_id: int, payload: MagiUpdate, _admin: AdminGate, bus: BusDep) -> MagiOut:
    _require_visible(bus, magi_id)
    if bus.runtime_state_book is None:
        raise MagiHTTPException(503, "runtime.unavailable", "runtime registry is unavailable")
    runtime = bus.runtime_state_book.rename(runtime_id=magi_id, backend_ref=payload.name)
    if runtime is None:
        raise MagiHTTPException(
            409, "runtime.not_provisioned", "MAGI has no control runtime record"
        )
    return await _magi_out_with_provider(bus, _membership_or_404(bus, magi_id))


def _set_lifecycle(bus: Bus, *, magi_id: int, desired_state: RuntimeDesiredState) -> RuntimeOut:
    _require_visible(bus, magi_id)
    if magi_id == _runtime_magi_id_optional():
        raise MagiHTTPException(
            409, "runtime.current_magi_protected", "Cannot stop the MAGI serving this request"
        )
    if bus.runtime_state_book is None:
        raise MagiHTTPException(503, "runtime.unavailable", "runtime registry is unavailable")
    existing = bus.runtime_state_book.get_by_runtime_id(runtime_id=magi_id)
    if existing is None or existing.backend_kind == "unprovisioned":
        raise MagiHTTPException(
            409, "runtime.not_provisioned", "Provision this MAGI before changing its lifecycle"
        )
    runtime = bus.runtime_state_book.set_desired_state(
        runtime_id=magi_id, desired_state=desired_state
    )
    result = _runtime_out(runtime)
    assert result is not None
    return result


def _runtime_magi_id_optional() -> int | None:
    raw = os.environ.get("MAGI_RUNTIME_ID")
    return int(raw) if raw and raw.isdigit() else None


@router.post("/magi/{magi_id}/runtime/start", response_model=RuntimeOut)
def start_runtime(magi_id: int, _admin: AdminGate, bus: BusDep) -> RuntimeOut:
    return _set_lifecycle(bus, magi_id=magi_id, desired_state=RuntimeDesiredState.STARTED)


@router.post("/magi/{magi_id}/runtime/stop", response_model=RuntimeOut)
def stop_runtime(magi_id: int, _admin: AdminGate, bus: BusDep) -> RuntimeOut:
    return _set_lifecycle(bus, magi_id=magi_id, desired_state=RuntimeDesiredState.STOPPED)


@router.delete("/magi/{magi_id}", status_code=204)
def delete_magi(magi_id: int, _admin: AdminGate, bus: BusDep) -> Response:
    _require_visible(bus, magi_id)
    if magi_id == _runtime_magi_id_optional():
        raise MagiHTTPException(
            409, "runtime.current_magi_protected", "Cannot delete the MAGI serving this request"
        )
    runtime = bus.runtime_state_book.get_by_runtime_id(runtime_id=magi_id) if bus.runtime_state_book else None
    if runtime is not None and runtime.backend_kind != "unprovisioned":
        raise MagiHTTPException(
            409,
            "runtime.deprovision_required",
            "Deprovision the runtime before removing its identity",
        )
    if runtime is not None and bus.runtime_state_book:
        bus.runtime_state_book.remove(runtime_id=magi_id)
    if not bus.memberships_book or not bus.memberships_book.remove(magi_id=magi_id):
        raise MagiHTTPException(404, "not_found.magi", "MAGI not found")
    return Response(status_code=204)


@self_router.get("/magi/self/instruction", response_model=InstructionOut)
def get_self_instruction(_admin: AdminGate, bus: BusDep) -> InstructionOut:
    magi_id = _runtime_magi_id()
    return InstructionOut(
        magi_id=magi_id,
        instruction=bus.settings_book.get_value(key="instruction") or "",
    )


@self_router.put("/magi/self/instruction", response_model=InstructionOut)
def put_self_instruction(
    payload: InstructionPayload, _admin: AdminGate, bus: BusDep
) -> InstructionOut:
    magi_id = _runtime_magi_id()
    bus.settings_book.set(key="instruction", value=payload.instruction)
    return InstructionOut(magi_id=magi_id, instruction=payload.instruction)


__all__ = ["router", "self_router"]
