"""Target-scoped login operations owned by a MAGI runtime.

The browser never reaches this router directly.  The singleton WebUI signs
these requests, while this runtime remains the source of truth for its local
assigned user and its direct MAGIS's administrators.  That keeps login codes,
Bot tokens and private contacts out of the WebUI service.

All data access goes through the bus facade — no ``magi.db`` imports
(``channels → db`` boundary).  Bot delivery still calls the
``channels.telegram.bot`` module directly because that's a
transport, not a database.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from datetime import UTC, datetime

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from old_bus import Bus
from old_bus.firmwares.books.local import Role
from old_bus.firmwares.books.magis import (
    AUTH_MODE_IM_2FA_ENABLED,
    AUTH_MODE_LOCAL_NO_2FA,
    AUTH_MODE_RECOVERY_LOCAL_NO_2FA,
)
from channels.api.dependencies import BusDep
from channels.api.errors import MagiHTTPException
from channels.api.proxy_auth import verified_proxy_operator, verified_proxy_scope
from channels.telegram import bot as tg_bot

router = APIRouter(tags=["runtime-access"])

_TTL_SECONDS = 300
_COOLDOWN_SECONDS = 30
_CODE_PREFIX = "auth.target_login_code"


class LoginAccount(BaseModel):
    contact_id: int  # runtime-local contacts.id (opaque; primary key)
    magis_admin_id: int | None = None  # shared identity for an admin account
    name: str
    role: str = "assigned"  # "admin" | "assigned" — explicit so the picker can disambiguate
    admin: bool = False
    assigned: bool = False
    has_tg_code: bool = False
    tgid: int | None = None  # TG chat id when bound; legacy key for the TG-code path
    auth_mode: str = "local_no_2fa"
    local_direct_allowed: bool = False


class LoginAccountsResponse(BaseModel):
    magi_id: int
    magis_id: int
    accounts: list[LoginAccount]


class LoginCodeRequest(BaseModel):
    contact_id: int
    role: str = "assigned"


class LoginCodeResponse(BaseModel):
    ok: bool
    expires_in: int = 0
    delivery: str | None = None
    error: str | None = None


class VerifyLoginCodeRequest(LoginCodeRequest):
    code: str = Field(min_length=6, max_length=6)


class VerifyLoginCodeResponse(BaseModel):
    ok: bool
    contact_id: int | None = None
    magis_admin_id: int | None = None
    role: str | None = None
    tgid: int | None = None
    display_name: str | None = None
    admin: bool = False
    assigned: bool = False
    error: str | None = None
    retry_after: int | None = None


class LocalDirectLoginRequest(LoginCodeRequest):
    """Pre-login request for a local-only admin session."""


class TwoFactorSendRequest(BaseModel):
    tgid: int


class TwoFactorVerifyRequest(TwoFactorSendRequest):
    code: str = Field(min_length=6, max_length=6)


def _require_webui(request: Request, bus: Bus) -> None:
    # Operator id 0 is the deliberately unauthenticated-before-login WebUI
    # caller.  It is still HMAC authenticated and target-bound.
    if verified_proxy_operator(bus, request) is None:
        raise MagiHTTPException(401, "access.unauthorized", "Invalid WebUI control request")


def _current_proxy_admin(request: Request, bus: Bus) -> int:
    """Return the shared admin identity from an authenticated runtime proxy call."""
    scope = verified_proxy_scope(bus, request)
    if scope is None:
        raise MagiHTTPException(401, "auth.not_signed_in", "Not signed in")
    is_admin, _assigned, _two_factor, admin_id = scope
    if not is_admin or admin_id is None or bus.magis_admins_book is None:
        raise MagiHTTPException(403, "auth.magis_admin_required", "MAGIS administrator required")
    admin = bus.magis_admins_book.get(admin_id)
    if admin is None:
        raise MagiHTTPException(403, "auth.magis_admin_required", "MAGIS administrator required")
    return admin.id


def _runtime_magi_id() -> int:
    value = os.environ.get("MAGI_RUNTIME_ID")
    if not value or not value.isdigit():
        raise MagiHTTPException(
            503, "access.runtime_identity_missing", "MAGI runtime identity is missing"
        )
    return int(value)


def _direct_magis(bus: Bus) -> tuple[int, int]:
    """Return this runtime's ``(magi_id, direct_magis_id)``.

    Surfaces the public-PG-backed MAGIS membership row through the
    bus so the channel layer never opens a ``magi.db.magis`` session
    directly.
    """
    magi_id = _runtime_magi_id()
    membership = bus.memberships_book.get(magi_id) if bus.memberships_book else None
    if membership is None:
        raise MagiHTTPException(409, "access.magi_unassigned", "MAGI is not assigned to a MAGIS")
    return magi_id, membership.magis_id


def _accounts(bus: Bus, magis_id: int) -> list[LoginAccount]:
    """Enumerate sign-in candidates for the local MAGI's direct MAGIS.

    An administrator's shared MAGIS identity is represented locally by a
    Contact projection.  The projection is used solely for local ownership
    (conversations and ActionItems); its ``magis_admin_id`` is the authority
    identity.  An assigned user remains a separate local account.

    ``contact_id`` is the runtime-local ``contacts.id`` —
    opaque from the wire but unique within the runtime. The
    ``role`` distinguishes the picker row, the cookie scope,
    and the runtime ``assigned=True`` check.
    """
    result: list[LoginAccount] = []

    # 1. MAGIS admins.  Each must have a local Contact projection before
    # it can sign in to this runtime.
    if bus.magis_admins_book is not None:
        for admin in bus.magis_admins_book.list_for_magis(magis_id=magis_id):
            contact = bus.contacts_book.get_by_magis_admin_id(magis_admin_id=admin.id)
            if contact is None:
                continue
            display = admin.name or contact.display_name or contact.name
            result.append(
                LoginAccount(
                    contact_id=contact.id,
                    magis_admin_id=admin.id,
                    name=display,
                    role="admin",
                    admin=True,
                    assigned=False,
                    has_tg_code=(
                        admin.auth_mode == AUTH_MODE_IM_2FA_ENABLED and admin.tgid is not None
                    ),
                    tgid=admin.tgid,
                    auth_mode=admin.auth_mode,
                    local_direct_allowed=admin.auth_mode
                    in {AUTH_MODE_LOCAL_NO_2FA, AUTH_MODE_RECOVERY_LOCAL_NO_2FA},
                )
            )

    # 2. Per-MAGI assigned users.
    for contact in (row for row in bus.contacts_book.list_all() if row.role == Role.ASSIGNED):
        # If a Genesis-admin row with the same contact_id
        # already exists, still produce an assigned row
        # so the picker offers both login scopes.
        display = contact.display_name or contact.name or ""
        result.append(
            LoginAccount(
                contact_id=contact.id,
                name=display,
                role="assigned",
                admin=False,
                assigned=True,
                has_tg_code=contact.tgid is not None,
                tgid=contact.tgid,
                auth_mode="im_2fa_enabled" if contact.tgid is not None else "disabled",
                local_direct_allowed=False,
            )
        )

    return result


def _code_key(contact_id: int, role: str) -> str:
    return f"{_CODE_PREFIX}.{role}.{contact_id}"


def _find_account(accounts: list[LoginAccount], contact_id: int, role: str) -> LoginAccount | None:
    for row in accounts:
        if row.contact_id == contact_id and row.role == role:
            return row
    return None


def _new_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def _code_hash(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


async def _send_code(bus: Bus, magi_id: int, magis_id: int, contact_id: int, role: str, text: str) -> str:
    _ = magi_id, magis_id
    account = _find_account(_accounts(bus, magis_id), contact_id, role)
    if (
        account is None
        or account.tgid is None
        or (account.admin and account.auth_mode != AUTH_MODE_IM_2FA_ENABLED)
    ):
        raise MagiHTTPException(
            409, "access.no_tg_binding", "This account has no Telegram binding"
        )
    bot_token = bus.settings_book.get_value(key="telegram.bot_token")
    if bot_token:
        await tg_bot.send_text_raw(bot_token, account.tgid, text)
        return "self"

    raise MagiHTTPException(409, "access.no_delivery_bot", "This MAGI has no Bot configured")


@router.get("/access/login-accounts", response_model=LoginAccountsResponse)
async def login_accounts(request: Request, bus: BusDep) -> LoginAccountsResponse:
    _require_webui(request, bus)
    magi_id, magis_id = _direct_magis(bus)
    accounts = sorted(
        _accounts(bus, magis_id),
        key=lambda row: (row.role, row.name.lower(), row.contact_id),
    )
    return LoginAccountsResponse(
        magi_id=magi_id,
        magis_id=magis_id,
        accounts=accounts,
    )


@router.post("/access/send-login-code", response_model=LoginCodeResponse)
async def send_login_code(
    payload: LoginCodeRequest, request: Request, bus: BusDep
) -> LoginCodeResponse:
    _require_webui(request, bus)
    magi_id, magis_id = _direct_magis(bus)
    account = _find_account(_accounts(bus, magis_id), payload.contact_id, payload.role)
    if (
        account is None
        or account.tgid is None
        or (account.admin and account.auth_mode != AUTH_MODE_IM_2FA_ENABLED)
    ):
        # Do not turn this into a principal-enumeration endpoint.
        return LoginCodeResponse(ok=True, expires_in=_TTL_SECONDS)
    previous_raw = bus.settings_book.get_value(key=_code_key(payload.contact_id, payload.role))
    if previous_raw:
        try:
            previous = json.loads(previous_raw)
            elapsed = datetime.now(UTC).timestamp() - float(previous.get("last_sent_at", 0))
            if elapsed < _COOLDOWN_SECONDS:
                return LoginCodeResponse(
                    ok=False,
                    error=f"Wait {int(_COOLDOWN_SECONDS - elapsed)}s before requesting a new code.",
                )
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
    code = _new_code()
    now = datetime.now(UTC)
    bus.settings_book.set(
        key=_code_key(payload.contact_id, payload.role),
        value=json.dumps(
            {
                "code_hash": _code_hash(code),
                "expires_at": now.timestamp() + _TTL_SECONDS,
                "last_sent_at": now.timestamp(),
            }
        ),
    )
    try:
        delivery = await _send_code(
            bus,
            magi_id,
            magis_id,
            payload.contact_id,
            payload.role,
            f"Your MAGI sign-in code is: <code>{code}</code>\n\nThis code expires in 5 minutes.",
        )
    except Exception as exc:
        bus.settings_book.delete_by_key(key=_code_key(payload.contact_id, payload.role))
        if isinstance(exc, MagiHTTPException):
            raise
        raise MagiHTTPException(
            503, "access.delivery_failed", "Could not deliver the login code"
        ) from exc
    return LoginCodeResponse(ok=True, expires_in=_TTL_SECONDS, delivery=delivery)


@router.post("/access/local-direct-login", response_model=VerifyLoginCodeResponse)
async def local_direct_login(
    payload: LocalDirectLoginRequest, request: Request, bus: BusDep
) -> VerifyLoginCodeResponse:
    """Return identity claims for a local-only bootstrap admin session.

    The browser reaches this only through the control-plane endpoint, which
    proves the deployment boundary.  The runtime is still authoritative for
    both the account role and the persisted operator auth state.
    """

    _require_webui(request, bus)
    _magi_id, magis_id = _direct_magis(bus)
    account = _find_account(_accounts(bus, magis_id), payload.contact_id, payload.role)
    if account is None or not account.admin or not account.local_direct_allowed:
        return VerifyLoginCodeResponse(ok=False, error="Local direct sign-in is unavailable")
    return VerifyLoginCodeResponse(
        ok=True,
        contact_id=account.contact_id,
        magis_admin_id=account.magis_admin_id,
        role=account.role,
        tgid=account.tgid,
        display_name=account.name,
        admin=True,
        assigned=account.assigned,
    )


@router.post("/access/verify-login-code", response_model=VerifyLoginCodeResponse)
async def verify_login_code(
    payload: VerifyLoginCodeRequest, request: Request, bus: BusDep
) -> VerifyLoginCodeResponse:
    _require_webui(request, bus)
    _magi_id, magis_id = _direct_magis(bus)
    account = _find_account(_accounts(bus, magis_id), payload.contact_id, payload.role)
    if account is None:
        return VerifyLoginCodeResponse(ok=False, error="Code does not match")
    raw = bus.settings_book.get_value(key=_code_key(payload.contact_id, payload.role))
    if not raw:
        return VerifyLoginCodeResponse(ok=False, error="No code was sent — request a new one.")
        bus.settings_book.delete_by_key(key=_code_key(payload.contact_id, payload.role))
    try:
        stored = json.loads(raw)
        valid = datetime.now(UTC).timestamp() < float(stored.get("expires_at", 0))
    except (TypeError, ValueError, json.JSONDecodeError):
        valid = False
        stored = {}
    if not valid:
        return VerifyLoginCodeResponse(ok=False, error="Code expired — request a new one.")
    if _code_hash(payload.code.strip()) != str(stored.get("code_hash", "")):
        return VerifyLoginCodeResponse(ok=False, error="Code does not match")
    return VerifyLoginCodeResponse(
        ok=True,
        contact_id=account.contact_id,
        magis_admin_id=account.magis_admin_id,
        role=account.role,
        tgid=account.tgid,
        display_name=account.name,
        admin=account.admin,
        assigned=account.assigned,
    )


@router.post("/access/two-factor/send-login-code", response_model=LoginCodeResponse)
async def send_two_factor_setup_code(
    payload: TwoFactorSendRequest, request: Request, bus: BusDep
) -> LoginCodeResponse:
    admin_id = _current_proxy_admin(request, bus)
    key = f"auth.two_factor_setup.{admin_id}"
    code = _new_code()
    now = datetime.now(UTC)
    bus.settings_book.set(
        key=key,
        value=json.dumps(
            {
                "tgid": payload.tgid,
                "code_hash": _code_hash(code),
                "expires_at": now.timestamp() + _TTL_SECONDS,
            }
        ),
    )
    token = bus.settings_book.get_value(key="telegram.bot_token")
    if not token:
        bus.settings_book.delete_by_key(key=key)
        raise MagiHTTPException(409, "access.no_delivery_bot", "Configure a Telegram bot first")
    try:
        await tg_bot.send_text_raw(
            token,
            payload.tgid,
            f"Your MAGI two-factor setup code is: <code>{code}</code>\n\nThis code expires in 5 minutes.",
        )
    except Exception as exc:
        bus.settings_book.delete_by_key(key=key)
        raise MagiHTTPException(503, "access.delivery_failed", "Could not deliver the code") from exc
    return LoginCodeResponse(ok=True, expires_in=_TTL_SECONDS, delivery="self")


@router.post("/access/two-factor/verify-login-code", response_model=LoginCodeResponse)
async def verify_two_factor_setup_code(
    payload: TwoFactorVerifyRequest, request: Request, bus: BusDep
) -> LoginCodeResponse:
    admin_id = _current_proxy_admin(request, bus)
    key = f"auth.two_factor_setup.{admin_id}"
    raw = bus.settings_book.get_value(key=key)
    bus.settings_book.delete_by_key(key=key)
    try:
        stored = json.loads(raw or "")
        valid = (
            int(stored.get("tgid")) == payload.tgid
            and str(stored.get("code_hash")) == _code_hash(payload.code.strip())
            and datetime.now(UTC).timestamp() < float(stored.get("expires_at", 0))
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        valid = False
    if not valid:
        return LoginCodeResponse(ok=False, error="Code does not match or expired")
    if bus.magis_admins_book is None:
        raise MagiHTTPException(503, "unavailable.magis_store", "MAGIS store unavailable")
    bus.magis_admins_book.bind_telegram(admin_id=admin_id, tgid=payload.tgid)
    projection = bus.contacts_book.get_by_magis_admin_id(magis_admin_id=admin_id)
    if projection is not None:
        from proactive.two_factor_action import reconcile_for_admin

        reconcile_for_admin(
            book=bus.action_items_book,
            contact_id=projection.id,
            auth_mode=AUTH_MODE_IM_2FA_ENABLED,
        )
    return LoginCodeResponse(ok=True)
