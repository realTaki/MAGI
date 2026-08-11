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
from datetime import UTC, datetime

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from magi.bus import Bus
from magi.channels.api.dependencies import BusDep
from magi.channels.api.errors import MagiHTTPException
from magi.channels.api.proxy_auth import verified_proxy_operator
from magi.channels.telegram import bot as tg_bot

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
    magi_id: int
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


def _require_webui(request: Request) -> None:
    # Operator id 0 is the deliberately unauthenticated-before-login WebUI
    # caller.  It is still HMAC authenticated and target-bound.
    if verified_proxy_operator(request) is None:
        raise MagiHTTPException(401, "access.unauthorized", "Invalid WebUI control request")


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
    membership = bus.memberships_book.get(magi_id=magi_id) if bus.memberships_book else None
    if membership is None:
        raise MagiHTTPException(409, "access.magi_unassigned", "MAGI is not assigned to a MAGIS")
    return magi_id, membership.magis_id


def _accounts(bus: Bus, magis_id: int) -> dict[int, LoginAccount]:
    """Enumerate sign-in candidates for the local MAGI's direct MAGIS."""
    result: dict[int, LoginAccount] = {}
    for admin in (
        bus.magis_admins_book.list_for_magis(magis_id=magis_id) if bus.magis_admins_book else []
    ):
        result[admin.contact_id] = LoginAccount(
            telegram_id=admin.contact_id,
            name=f"Admin {admin.contact_id}",
            admin=True,
            assigned=False,
        )
    for contact in (row for row in bus.contacts_book.list_all() if row.role == "assigned"):
        tg = contact.telegram_id
        if tg is None:
            continue
        existing = result.get(tg)
        display = contact.display_name or contact.name or ""
        if existing is None:
            result[tg] = LoginAccount(
                telegram_id=tg,
                name=display,
                admin=False,
                assigned=True,
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


async def _send_code(bus: Bus, magi_id: int, magis_id: int, telegram_id: int, text: str) -> str:
    _ = magi_id, magis_id
    bot_token = bus.settings_book.get(key="telegram.bot_token")
    if bot_token:
        await tg_bot.send_text_raw(bot_token, telegram_id, text)
        return "self"

    raise MagiHTTPException(409, "access.no_delivery_bot", "This MAGI has no Bot configured")


@router.get("/access/login-accounts", response_model=LoginAccountsResponse)
async def login_accounts(request: Request, bus: BusDep) -> LoginAccountsResponse:
    _require_webui(request)
    magi_id, magis_id = _direct_magis(bus)
    return LoginAccountsResponse(
        magi_id=magi_id,
        magis_id=magis_id,
        accounts=sorted(
            _accounts(bus, magis_id).values(), key=lambda row: (row.name.lower(), row.telegram_id)
        ),
    )


@router.post("/access/send-login-code", response_model=LoginCodeResponse)
async def send_login_code(
    payload: LoginCodeRequest, request: Request, bus: BusDep
) -> LoginCodeResponse:
    _require_webui(request)
    magi_id, magis_id = _direct_magis(bus)
    account = _accounts(bus, magis_id).get(payload.telegram_id)
    if account is None:
        # Do not turn this into a principal-enumeration endpoint.
        return LoginCodeResponse(ok=True, expires_in=_TTL_SECONDS)
    previous_raw = bus.settings_book.get(key=_code_key(payload.telegram_id))
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
        key=_code_key(payload.telegram_id),
        value=json.dumps(
            {
                "code": code,
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
            payload.telegram_id,
            f"Your MAGI sign-in code is: <code>{code}</code>\n\nThis code expires in 5 minutes.",
        )
    except Exception as exc:
        bus.settings_book.delete(key=_code_key(payload.telegram_id))
        if isinstance(exc, MagiHTTPException):
            raise
        raise MagiHTTPException(
            503, "access.delivery_failed", "Could not deliver the login code"
        ) from exc
    return LoginCodeResponse(ok=True, expires_in=_TTL_SECONDS, delivery=delivery)


@router.post("/access/verify-login-code", response_model=VerifyLoginCodeResponse)
async def verify_login_code(
    payload: VerifyLoginCodeRequest, request: Request, bus: BusDep
) -> VerifyLoginCodeResponse:
    _require_webui(request)
    _magi_id, magis_id = _direct_magis(bus)
    account = _accounts(bus, magis_id).get(payload.telegram_id)
    if account is None:
        return VerifyLoginCodeResponse(ok=False, error="Code does not match")
    raw = bus.settings_book.get(key=_code_key(payload.telegram_id))
    if not raw:
        return VerifyLoginCodeResponse(ok=False, error="No code was sent — request a new one.")
    bus.settings_book.delete(key=_code_key(payload.telegram_id))
    try:
        stored = json.loads(raw)
        valid = datetime.now(UTC).timestamp() < float(stored.get("expires_at", 0))
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
