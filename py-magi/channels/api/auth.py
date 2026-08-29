"""WebUI authentication for a selected MAGI runtime.

Passwords and onboarding are intentionally absent.  A MAGIS administrator is
the shared authority and authentication identity; the selected runtime exposes
its local Contact projection only as the owner of local data.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from base64 import urlsafe_b64decode, urlsafe_b64encode
from datetime import UTC, datetime
from typing import Any

import httpx
from fastapi import APIRouter, Cookie, Request, Response
from pydantic import BaseModel, Field

from old_bus import Bus
from old_bus.firmwares.books.magis.runtimeBook import RuntimeObservedState
from channels.api import control_store
from channels.api.dependencies import BusDep
from channels.api.errors import MagiHTTPException
from channels.api.proxy_auth import build_proxy_headers
from channels.api.runtime_http import CONTROL_TIMEOUT, RELAY_TIMEOUT

logger = logging.getLogger("magi.api.auth")
router = APIRouter(tags=["auth"])

SESSION_COOKIE_NAME = "magi_session"
SESSION_TTL_SECONDS = 14 * 24 * 60 * 60


def _signing_key(bus: Bus) -> bytes:
    # Control-plane mode (WebUI talking to its MAGIS-managed runtimes):
    # the session signing key is derived from the per-MAGIS
    # ``control_secrets`` row. The DB row is the single source of truth —
    # ``_ensure_control_secret`` provisions it during ``magi init``. Both
    # the WebUI and the node subprocesses share the row because they
    # both open the MAGIS store.
    # ``bus.settings_book`` is intentionally NOT consulted here: the WebUI
    # bus is bound to the MAGIS store, not the node's SQLite, and a
    # silent fallback would let a partially-provisioned node serve
    # cookies that the rest of the control plane cannot verify.
    if control_store.enabled():
        if bus.control_secrets_book is None:
            raise RuntimeError(
                "bus.control_secrets_book is unavailable; the MAGIS store "
                "is not wired into this Bus. Run `magi init` to provision "
                "it."
            )
        row = bus.control_secrets_book.get_by_name(name=_control_secret_name(bus))
        if row is None or row.secret_value is None:
            raise RuntimeError(
                "control_secrets row is missing for the current MAGIS; "
                "run `magi init` to provision the control secret."
            )
        return hashlib.sha256(row.secret_value + b"magi-control-session").digest()
    # Standalone / per-node mode (e.g. k8s pod running its own control bus
    # directly, no shared MAGIS). The signing key is stored in the
    # node's ``settings`` table, freshly generated if absent so first-boot
    # sign-in still works.
    raw = bus.settings_book.get_value(key="auth.signing_key")
    if raw:
        return hashlib.sha256(raw.encode() + b"magi-session-signing").digest()
    import secrets

    return secrets.token_bytes(32)


def _control_secret_name(bus: Bus) -> str:
    """Resolve the MAGIS-name under which ``control_secrets`` is keyed."""
    getter = getattr(bus, "magis_name", None)
    if isinstance(getter, str) and getter:
        return getter
    return os.environ.get("MAGIS_NAME", "default")


def _optional_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.lstrip("-").isdigit():
        return int(value)
    return None


def _sign_selected_session(
    bus: Bus,
    *,
    magi_id: int,
    contact_id: int,
    magis_admin_id: int | None,
    tgid: int | None,
    display_name: str | None,
    admin: bool,
    assigned: bool,
    two_factor: bool,
) -> str:
    """Create a selected-MAGI session; v5 invalidates every legacy cookie."""
    if admin != (magis_admin_id is not None):
        raise ValueError("admin sessions must carry exactly one magis_admin_id")
    payload = {
        "v": 5,
        "magi_id": magi_id,
        "contact_id": contact_id,
        "magis_admin_id": magis_admin_id,
        "tgid": tgid,
        "display_name": display_name,
        "admin": admin,
        "assigned": assigned,
        "two_factor": two_factor,
        "ts": int(datetime.now(UTC).timestamp()),
    }
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
    signature = hmac.new(_signing_key(bus), raw, hashlib.sha256).hexdigest()[:24]
    return "v5." + urlsafe_b64encode(raw).decode().rstrip("=") + "." + signature


def selected_session(bus: Bus, token: str | None) -> dict[str, Any] | None:
    """Validate a v5 cookie without resolving local persistence."""
    if not token or not token.startswith("v5."):
        return None
    try:
        _version, body, signature = token.split(".", 2)
        raw = urlsafe_b64decode(body + "=" * (-len(body) % 4))
        expected = hmac.new(_signing_key(bus), raw, hashlib.sha256).hexdigest()[:24]
        if not hmac.compare_digest(signature, expected):
            return None
        payload = json.loads(raw)
        if (
            payload.get("v") != 5
            or not isinstance(payload.get("magi_id"), int)
            or not isinstance(payload.get("contact_id"), int)
            or not isinstance(payload.get("admin"), bool)
            or not isinstance(payload.get("assigned"), bool)
            or not isinstance(payload.get("two_factor"), bool)
        ):
            return None
        admin_id = payload.get("magis_admin_id")
        if payload["admin"] != isinstance(admin_id, int):
            return None
        if payload.get("tgid") is not None and not isinstance(payload["tgid"], int):
            return None
        if datetime.now(UTC).timestamp() - int(payload.get("ts", 0)) > SESSION_TTL_SECONDS:
            return None
        return payload
    except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError):
        return None


def resolve_session(bus: Bus, token: str | None) -> dict[str, Any] | None:
    """The only supported session shape is the selected-MAGI v5 session."""
    return selected_session(bus, token)


class AvailableMAGI(BaseModel):
    id: int
    name: str | None = None


class AvailableMAGIResponse(BaseModel):
    magi: list[AvailableMAGI]


class TargetLoginRequest(BaseModel):
    contact_id: int
    role: str = "assigned"


class TargetVerifyRequest(TargetLoginRequest):
    code: str = Field(min_length=6, max_length=6)


class TargetLoginResult(BaseModel):
    ok: bool
    error: str | None = None


class MeResponse(BaseModel):
    contact_id: int
    magis_admin_id: int | None = None
    tgid: int | None = None
    display_name: str | None = None
    admin: bool
    assigned: bool
    selected_magi_id: int
    two_factor_enabled: bool
    authentication_mode: str


async def _target_access(
    bus: Bus,
    magi_id: int,
    method: str,
    path: str,
    payload: dict[str, object] | None = None,
    timeout: httpx.Timeout = CONTROL_TIMEOUT,
) -> dict[str, Any]:
    runtime = bus.runtime_state_book.get_by_runtime_id(runtime_id=magi_id) if bus.runtime_state_book else None
    base = getattr(runtime, "base_url", None) if runtime else None
    if not base:
        raise MagiHTTPException(503, "access.runtime_unreachable", "Selected MAGI runtime is unreachable")
    headers = build_proxy_headers(
        bus=bus,
        method=method,
        path_and_query=path,
        target_id=magi_id,
        operator_id=0,
        operator_name="WebUI login",
        tgid=None,
        admin=False,
        assigned=False,
    )
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.request(method, base + path, json=payload, headers=headers)
    except httpx.HTTPError as exc:
        raise MagiHTTPException(
            503, "access.runtime_unreachable", "Selected MAGI runtime is unreachable"
        ) from exc
    try:
        body = response.json()
    except ValueError:
        body = {"detail": "Selected MAGI returned an invalid response"}
    if response.is_error:
        raise MagiHTTPException(
            response.status_code,
            str(body.get("code", "access.target_error")),
            str(body.get("detail", "Target request failed")),
        )
    return body


@router.get("/available-magi", response_model=AvailableMAGIResponse)
async def available_magi(bus: BusDep) -> AvailableMAGIResponse:
    runtimes = bus.runtime_state_book
    if runtimes is None:
        return AvailableMAGIResponse(magi=[])
    active = {RuntimeObservedState.STARTING, RuntimeObservedState.STARTED}
    return AvailableMAGIResponse(
        magi=[
            AvailableMAGI(id=item.runtime_id, name=item.backend_ref)
            for item in runtimes.list_all()
            if item.observed_state in active
        ]
    )


@router.get("/targets/{magi_id}/accounts")
async def target_accounts(magi_id: int, bus: BusDep) -> dict[str, Any]:
    """The selected runtime is authoritative for both admin and local accounts."""
    return await _target_access(bus, magi_id, "GET", "/api/access/login-accounts")


@router.post("/targets/{magi_id}/send-login-code")
async def target_send_login_code(
    magi_id: int, payload: TargetLoginRequest, bus: BusDep
) -> dict[str, Any]:
    return await _target_access(
        bus,
        magi_id,
        "POST",
        "/api/access/send-login-code",
        payload.model_dump(),
        timeout=RELAY_TIMEOUT,
    )


def _set_session_from_target(
    *, bus: Bus, magi_id: int, result: dict[str, Any], response: Response, two_factor: bool
) -> None:
    contact_id = result.get("contact_id")
    if not isinstance(contact_id, int):
        raise MagiHTTPException(502, "access.target_invalid_identity", "Invalid target identity")
    admin = bool(result.get("admin"))
    magis_admin_id = _optional_int(result.get("magis_admin_id"))
    if admin and magis_admin_id is None:
        raise MagiHTTPException(502, "access.target_invalid_identity", "Admin has no MAGIS identity")
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=_sign_selected_session(
            bus,
            magi_id=magi_id,
            contact_id=contact_id,
            magis_admin_id=magis_admin_id if admin else None,
            tgid=_optional_int(result.get("tgid")),
            display_name=result.get("display_name") if isinstance(result.get("display_name"), str) else None,
            admin=admin,
            assigned=bool(result.get("assigned")),
            two_factor=two_factor,
        ),
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        path="/",
    )


@router.post("/targets/{magi_id}/verify-login-code", response_model=TargetLoginResult)
async def target_verify_login_code(
    magi_id: int, payload: TargetVerifyRequest, response: Response, bus: BusDep
) -> TargetLoginResult:
    result = await _target_access(
        bus, magi_id, "POST", "/api/access/verify-login-code", payload.model_dump()
    )
    if not result.get("ok"):
        return TargetLoginResult(ok=False, error=str(result.get("error") or "Code does not match"))
    _set_session_from_target(bus=bus, magi_id=magi_id, result=result, response=response, two_factor=True)
    return TargetLoginResult(ok=True)


@router.post("/targets/{magi_id}/local-direct-login", response_model=TargetLoginResult)
async def target_local_direct_login(
    magi_id: int,
    payload: TargetLoginRequest,
    response: Response,
    bus: BusDep,
) -> TargetLoginResult:
    # NOTE: deployment boundary (loopback-only, public-IP-only, VPN-only,
    # …) is the operator's call — enforce it in nginx / auth-proxy / VPN /
    # private network, not here. The application is authn/authz-scoped
    # only; access scoping belongs to the network/ops layer.
    result = await _target_access(
        bus, magi_id, "POST", "/api/access/local-direct-login", payload.model_dump()
    )
    if not result.get("ok"):
        return TargetLoginResult(
            ok=False, error=str(result.get("error") or "Local direct sign-in is unavailable")
        )
    _set_session_from_target(bus=bus, magi_id=magi_id, result=result, response=response, two_factor=False)
    return TargetLoginResult(ok=True)


@router.post("/logout", status_code=204)
async def logout(response: Response) -> Response:
    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/")
    return Response(status_code=204)


@router.get("/me", response_model=MeResponse)
async def me(
    bus: BusDep,
    magi_session: str | None = Cookie(default=None),
) -> MeResponse:
    session = resolve_session(bus, magi_session)
    if session is None:
        raise MagiHTTPException(401, "auth.not_signed_in", "Not signed in")
    admin = bool(session["admin"])
    return MeResponse(
        contact_id=int(session["contact_id"]),
        magis_admin_id=_optional_int(session.get("magis_admin_id")),
        tgid=_optional_int(session.get("tgid")),
        display_name=session.get("display_name") if isinstance(session.get("display_name"), str) else None,
        admin=admin,
        assigned=bool(session["assigned"]),
        selected_magi_id=int(session["magi_id"]),
        two_factor_enabled=bool(session.get("two_factor")),
        authentication_mode=(
            "im_2fa_enabled" if bool(session.get("two_factor")) else "local_no_2fa"
        ),
    )


__all__ = ["SESSION_COOKIE_NAME", "resolve_session", "selected_session", "router"]
