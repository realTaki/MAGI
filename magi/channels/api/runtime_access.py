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
    contact_id: int  # runtime-local contacts.id (opaque; primary key)
    name: str
    role: str = "assigned"  # "admin" | "assigned" — explicit so the picker can disambiguate
    admin: bool = False
    assigned: bool = False
    has_password: bool = False
    has_tg_code: bool = False
    tgid: int | None = None  # TG chat id when bound; legacy key for the TG-code path


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
    role: str | None = None
    tgid: int | None = None
    display_name: str | None = None
    admin: bool = False
    assigned: bool = False
    error: str | None = None
    retry_after: int | None = None


class LoginPasswordRequest(BaseModel):
    contact_id: int
    role: str = "assigned"
    password: str


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


def _accounts(bus: Bus, magis_id: int) -> list[LoginAccount]:
    """Enumerate sign-in candidates for the local MAGI's direct MAGIS.

    A person who appears as both Genesis admin AND per-MAGI
    assigned produces two picker rows — each login grants a
    different scope of permissions.

    ``contact_id`` is the runtime-local ``contacts.id`` —
    opaque from the wire but unique within the runtime. The
    ``role`` distinguishes the picker row, the cookie scope,
    and the runtime ``assigned=True`` check.
    """
    result: list[LoginAccount] = []

    # 1. Genesis admins (MAGIS-side). The runtime may or may
    # not have MAGIS DB access; if it doesn't (the typical
    # webui-only deploy), the webui adds Genesis admins
    # separately before responding to the picker.
    if bus.magis_admins_book is not None:
        for admin in bus.magis_admins_book.list_for_magis(magis_id=magis_id):
            contact = bus.contacts_book.get(contact_id=admin.contact_id)
            display = (contact.display_name or contact.name) if contact else f"Admin #{admin.contact_id}"
            has_pw = contact is not None and bus.contacts_book.get_password_hash(contact_id=admin.contact_id) is not None
            result.append(
                LoginAccount(
                    contact_id=admin.contact_id,
                    name=display or f"Admin #{admin.contact_id}",
                    role="admin",
                    admin=True,
                    assigned=contact is not None and contact.role == "assigned",
                    has_password=has_pw,
                    has_tg_code=contact is not None and contact.tgid is not None,
                    tgid=contact.tgid if contact else None,
                )
            )

    # 2. Per-MAGI assigned users. Includes webui-only users
    # (no TG binding) so the wizard's password-only path
    # shows up in the picker.
    for contact in (row for row in bus.contacts_book.list_all() if row.role == "assigned"):
        # If a Genesis-admin row with the same contact_id
        # already exists, still produce an assigned row
        # so the picker offers both login scopes.
        display = contact.display_name or contact.name or ""
        has_pw = bus.contacts_book.get_password_hash(contact_id=contact.id) is not None
        result.append(
            LoginAccount(
                contact_id=contact.id,
                name=display,
                role="assigned",
                admin=False,
                assigned=True,
                has_password=has_pw,
                has_tg_code=contact.tgid is not None,
                tgid=contact.tgid,
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


async def _send_code(bus: Bus, magi_id: int, magis_id: int, contact_id: int, role: str, text: str) -> str:
    _ = magi_id, magis_id
    account = _find_account(_accounts(bus, magis_id), contact_id, role)
    if account is None or account.tgid is None:
        raise MagiHTTPException(
            409, "access.no_tg_binding", "This account has no Telegram binding"
        )
    bot_token = bus.settings_book.get(key="telegram.bot_token")
    if bot_token:
        await tg_bot.send_text_raw(bot_token, account.tgid, text)
        return "self"

    raise MagiHTTPException(409, "access.no_delivery_bot", "This MAGI has no Bot configured")


@router.get("/access/login-accounts", response_model=LoginAccountsResponse)
async def login_accounts(request: Request, bus: BusDep) -> LoginAccountsResponse:
    _require_webui(request)
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
    _require_webui(request)
    magi_id, magis_id = _direct_magis(bus)
    account = _find_account(_accounts(bus, magis_id), payload.contact_id, payload.role)
    if account is None or account.tgid is None:
        # Do not turn this into a principal-enumeration endpoint.
        return LoginCodeResponse(ok=True, expires_in=_TTL_SECONDS)
    previous_raw = bus.settings_book.get(key=_code_key(payload.contact_id, payload.role))
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
            payload.contact_id,
            payload.role,
            f"Your MAGI sign-in code is: <code>{code}</code>\n\nThis code expires in 5 minutes.",
        )
    except Exception as exc:
        bus.settings_book.delete(key=_code_key(payload.contact_id, payload.role))
        if isinstance(exc, MagiHTTPException):
            raise
        raise MagiHTTPException(
            503, "access.delivery_failed", "Could not deliver the login code"
        ) from exc
    return LoginCodeResponse(ok=True, expires_in=_TTL_SECONDS, delivery=delivery)


@router.post("/access/login-password", response_model=VerifyLoginCodeResponse)
async def access_login_password(
    payload: LoginPasswordRequest, request: Request, bus: BusDep
) -> VerifyLoginCodeResponse:
    """Verify a password against this runtime's local ``contacts`` table.

    Called by the singleton webui via the proxy layer. The
    webui hands us ``(contact_id, role, password)``; we
    look up the local contact, verify the scrypt hash, and
    return the cookie inputs (``display_name``, ``admin``,
    ``assigned``, ``tgid``).

    Anti-enumeration: a ``contact_id`` the picker did not
    offer (and that therefore has no password_hash) responds
    as ``ok=False`` with a generic error and never sets a
    cookie on the calling webui.
    """
    from magi.channels.api import password_utils

    _require_webui(request)
    _magi_id, magis_id = _direct_magis(bus)
    account = _find_account(_accounts(bus, magis_id), payload.contact_id, payload.role)
    if account is None or not account.has_password:
        return VerifyLoginCodeResponse(ok=False, error="password does not match")

    if not password_utils.check_cooldown(
        bus,
        f"{payload.role}:{payload.contact_id}",
        cooldown_seconds=60,
    ):
        record = password_utils._store_get(bus, f"{payload.role}:{payload.contact_id}") or {}
        last = float(record.get("last_attempt_at", 0))
        remaining = max(1, int(60 - (_now_ts() - last)))
        return VerifyLoginCodeResponse(
            ok=False,
            error=f"Wait {remaining}s before trying again.",
            retry_after=remaining,
        )

    password_utils.record_attempt(bus, f"{payload.role}:{payload.contact_id}")

    stored = _password_hash(bus, payload.contact_id)
    if not stored or not password_utils.verify_password(stored, payload.password):
        return VerifyLoginCodeResponse(
            ok=False, error="password does not match", retry_after=60
        )

    password_utils.clear_attempt(bus, f"{payload.role}:{payload.contact_id}")
    contact = bus.contacts_book.get(contact_id=payload.contact_id)
    if contact is None:
        return VerifyLoginCodeResponse(ok=False, error="contact not found")
    return VerifyLoginCodeResponse(
        ok=True,
        contact_id=contact.id,
        role=payload.role,
        tgid=contact.tgid,
        display_name=contact.display_name or contact.name or "",
        admin=payload.role == "admin",
        assigned=payload.role == "assigned" and contact.role == "assigned",
    )


def _password_hash(bus: Bus, contact_id: int) -> str | None:
    return bus.contacts_book.get_password_hash(contact_id=contact_id)


def _now_ts() -> float:
    import time
    return time.time()


@router.post("/access/verify-login-code", response_model=VerifyLoginCodeResponse)
async def verify_login_code(
    payload: VerifyLoginCodeRequest, request: Request, bus: BusDep
) -> VerifyLoginCodeResponse:
    _require_webui(request)
    _magi_id, magis_id = _direct_magis(bus)
    account = _find_account(_accounts(bus, magis_id), payload.contact_id, payload.role)
    if account is None:
        return VerifyLoginCodeResponse(ok=False, error="Code does not match")
    raw = bus.settings_book.get(key=_code_key(payload.contact_id, payload.role))
    if not raw:
        return VerifyLoginCodeResponse(ok=False, error="No code was sent — request a new one.")
    bus.settings_book.delete(key=_code_key(payload.contact_id, payload.role))
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
        contact_id=account.contact_id,
        role=account.role,
        tgid=account.tgid,
        display_name=account.name,
        admin=account.admin,
        assigned=account.assigned,
    )
