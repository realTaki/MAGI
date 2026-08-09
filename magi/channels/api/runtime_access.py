"""Target-scoped login operations owned by a MAGI runtime.

The browser never reaches this router directly.  The singleton WebUI signs
these requests, while this runtime remains the source of truth for its local
assigned user and its direct MAGIS's administrators.  That keeps login codes,
Bot tokens and private contacts out of the WebUI service.

All data access goes through the bus facade — no ``magi.db`` imports
(``channels → db`` boundary).  Bot delivery still calls the
``magi.channels.telegram.bot`` module directly because that's a
transport, not a database.
"""

from __future__ import annotations

import json
import os
import secrets
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from magi.channels.api._bus import bus
from magi.channels.telegram import bot as tg_bot
from magi.channels.api.errors import MagiHTTPException
from magi.channels.api.proxy_auth import build_proxy_headers, verified_proxy_operator

router = APIRouter(tags=["runtime-access"])

_TTL_SECONDS = 300
_COOLDOWN_SECONDS = 60
_CODE_PREFIX = "auth.target_login_code"


class LoginAccount(BaseModel):
    telegram_id: int
    name: str
    admin: bool
    assigned: bool


class LoginAccountsResponse(BaseModel):
    magic_id: int
    magis_id: int
    accounts: list[LoginAccount]


class LoginCodeRequest(BaseModel):
    telegram_id: int


class LoginCodeResponse(BaseModel):
    ok: bool
    expires_in: int = 0
    delivery: str | None = None
    error: str | None = None


class VerifyLoginCodeRequest(LoginCodeRequest):
    code: str = Field(min_length=6, max_length=6)


class VerifyLoginCodeResponse(BaseModel):
    ok: bool
    telegram_id: int | None = None
    display_name: str | None = None
    admin: bool = False
    assigned: bool = False
    error: str | None = None


def _bus():
    """Resolve the bus facade for this runtime's state dir."""
    return bus


def _require_webui(request: Request) -> None:
    # Operator id 0 is the deliberately unauthenticated-before-login WebUI
    # caller.  It is still HMAC authenticated and target-bound.
    if verified_proxy_operator(request) is None:
        raise MagiHTTPException(401, "access.unauthorized", "Invalid WebUI control request")


def _runtime_magic_id() -> int:
    value = os.environ.get("MAGI_RUNTIME_ID")
    if not value or not value.isdigit():
        raise MagiHTTPException(503, "access.runtime_identity_missing", "MAGI runtime identity is missing")
    return int(value)


def _direct_magis() -> tuple[int, int]:
    """Return this runtime's ``(magic_id, direct_magis_id)``.

    Surfaces the public-PG-backed MAGIS membership row through the
    bus so the channel layer never opens a ``magi.db.magis`` session
    directly.
    """
    magic_id = _runtime_magic_id()
    bus = _bus()
    members = bus.magis.list_memberships_for_magic(magic_id)
    if not members:
        raise MagiHTTPException(409, "access.magic_unassigned", "MAGI is not assigned to a MAGIS")
    return magic_id, members[0].group_id


def _accounts(magis_id: int) -> dict[int, LoginAccount]:
    """Enumerate sign-in candidates for the local MAGI's direct MAGIS."""
    bus = _bus()
    result: dict[int, LoginAccount] = {}
    for admin in bus.magis.list_admin_accounts(magis_id):
        result[admin.magic_id] = LoginAccount(
            telegram_id=admin.magic_id,
            name=f"Admin {admin.magic_id}",
            admin=True,
            assigned=False,
        )
    for contact in bus.contacts.list_assigned():
        tg = contact.telegram_id
        if tg is None:
            continue
        existing = result.get(tg)
        display = (contact.display_name or contact.name or "")
        if existing is None:
            result[tg] = LoginAccount(
                telegram_id=tg, name=display, admin=False, assigned=True,
            )
        else:
            existing.assigned = True
            if not existing.name and display:
                existing.name = display
    return result


def _code_key(telegram_id: int) -> str:
    return f"{_CODE_PREFIX}.{telegram_id}"


def _new_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


async def _send_code(magic_id: int, magis_id: int, telegram_id: int, text: str) -> str:
    bus = _bus()
    bot_token = bus.settings.get("telegram.bot_token")
    if bot_token:
        await tg_bot.send_text_raw(bot_token, telegram_id, text)
        return "self"

    fallback = bus.magis.adam_url(magis_id, magic_id)
    if fallback is None:
        raise MagiHTTPException(
            409,
            "access.no_delivery_bot",
            "This MAGI has no Bot and its direct MAGIS ADAM Bot is unavailable",
        )
    adam_id, base = fallback
    path = "/api/control/telegram/send"
    headers = build_proxy_headers(
        method="POST", path_and_query=path, target_id=adam_id,
        operator_id=0, operator_name="MAGI login fallback", telegram_id=None,
    )
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(base + path, json={"telegram_id": telegram_id, "text": text}, headers=headers)
    if response.is_error:
        raise MagiHTTPException(503, "access.fallback_delivery_failed", "ADAM Bot could not deliver the login code")
    return "adam_fallback"


@router.get("/access/login-accounts", response_model=LoginAccountsResponse)
async def login_accounts(request: Request) -> LoginAccountsResponse:
    _require_webui(request)
    magic_id, magis_id = _direct_magis()
    return LoginAccountsResponse(
        magic_id=magic_id,
        magis_id=magis_id,
        accounts=sorted(_accounts(magis_id).values(), key=lambda row: (row.name.lower(), row.telegram_id)),
    )


@router.post("/access/send-login-code", response_model=LoginCodeResponse)
async def send_login_code(payload: LoginCodeRequest, request: Request) -> LoginCodeResponse:
    _require_webui(request)
    magic_id, magis_id = _direct_magis()
    bus = _bus()
    account = _accounts(magis_id).get(payload.telegram_id)
    if account is None:
        # Do not turn this into a principal-enumeration endpoint.
        return LoginCodeResponse(ok=True, expires_in=_TTL_SECONDS)
    previous_raw = bus.settings.get(_code_key(payload.telegram_id))
    if previous_raw:
        try:
            previous = json.loads(previous_raw)
            elapsed = datetime.now(timezone.utc).timestamp() - float(previous.get("last_sent_at", 0))
            if elapsed < _COOLDOWN_SECONDS:
                return LoginCodeResponse(ok=False, error=f"Wait {int(_COOLDOWN_SECONDS - elapsed)}s before requesting a new code.")
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
    code = _new_code()
    now = datetime.now(timezone.utc)
    bus.settings.set(_code_key(payload.telegram_id), json.dumps({
        "code": code, "expires_at": now.timestamp() + _TTL_SECONDS, "last_sent_at": now.timestamp(),
    }))
    try:
        delivery = await _send_code(
            magic_id, magis_id, payload.telegram_id,
            f"Your MAGI sign-in code is: <code>{code}</code>\n\nThis code expires in 5 minutes.",
        )
    except Exception as exc:
        bus.settings.delete(_code_key(payload.telegram_id))
        if isinstance(exc, MagiHTTPException):
            raise
        raise MagiHTTPException(503, "access.delivery_failed", "Could not deliver the login code") from exc
    return LoginCodeResponse(ok=True, expires_in=_TTL_SECONDS, delivery=delivery)


@router.post("/access/verify-login-code", response_model=VerifyLoginCodeResponse)
async def verify_login_code(payload: VerifyLoginCodeRequest, request: Request) -> VerifyLoginCodeResponse:
    _require_webui(request)
    _magic_id, magis_id = _direct_magis()
    bus = _bus()
    account = _accounts(magis_id).get(payload.telegram_id)
    if account is None:
        return VerifyLoginCodeResponse(ok=False, error="Code does not match")
    raw = bus.settings.get(_code_key(payload.telegram_id))
    if not raw:
        return VerifyLoginCodeResponse(ok=False, error="No code was sent — request a new one.")
    bus.settings.delete(_code_key(payload.telegram_id))
    try:
        stored = json.loads(raw)
        valid = datetime.now(timezone.utc).timestamp() < float(stored.get("expires_at", 0))
    except (TypeError, ValueError, json.JSONDecodeError):
        valid = False
        stored = {}
    if not valid:
        return VerifyLoginCodeResponse(ok=False, error="Code expired — request a new one.")
    if payload.code.strip() != str(stored.get("code", "")):
        return VerifyLoginCodeResponse(ok=False, error="Code does not match")
    return VerifyLoginCodeResponse(
        ok=True,
        telegram_id=account.telegram_id,
        display_name=account.name,
        admin=account.admin,
        assigned=account.assigned,
    )
