"""Target-scoped login operations owned by a MAGI runtime.

The browser never reaches this router directly.  The singleton WebUI signs
these requests, while this runtime remains the source of truth for its local
assigned user and its direct MAGIS's administrators.  That keeps login codes,
Bot tokens and private contacts out of the WebUI service.
"""

from __future__ import annotations

import json
import os
import secrets
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from magi.db import Contact, EveRuntime, MAGIC, MAGIS, MAGISAdmin, MAGISMembership, open_session, require_state_dir
from magi.db.magis import open_magis_session
from magi.db.settings import state_delete, state_get, state_set
from magi.channels.telegram import bot as tg_bot
from magi.channels.webui.api.errors import MagiHTTPException
from magi.channels.webui.proxy_auth import build_proxy_headers, verified_proxy_operator

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
    """Return this runtime's ``(magic_id, direct_magis_id)``."""
    magic_id = _runtime_magic_id()
    with open_magis_session() as session:
        binding = session.scalar(
            select(MAGISMembership).where(MAGISMembership.magic_id == magic_id)
        )
        if binding is None:
            raise MagiHTTPException(409, "access.magic_unassigned", "MAGI is not assigned to a MAGIS")
        return magic_id, binding.magis_id


def _accounts(magis_id: int) -> dict[int, LoginAccount]:
    result: dict[int, LoginAccount] = {}
    with open_magis_session() as session:
        for row in session.scalars(
            select(MAGISAdmin).where(MAGISAdmin.magis_id == magis_id)
        ).all():
            result[row.telegram_id] = LoginAccount(
                telegram_id=row.telegram_id,
                name=row.display_name or f"Admin {row.telegram_id}",
                admin=True,
                assigned=False,
            )
    with open_session() as session:
        for contact in session.scalars(
            select(Contact).where(
                Contact.role == "assigned",
                Contact.telegram_id.is_not(None),
                Contact.separated_at.is_(None),
            )
        ).all():
            assert contact.telegram_id is not None
            existing = result.get(contact.telegram_id)
            if existing is None:
                result[contact.telegram_id] = LoginAccount(
                    telegram_id=contact.telegram_id,
                    name=contact.display_name or contact.name,
                    admin=False,
                    assigned=True,
                )
            else:
                existing.assigned = True
                if not existing.name and (contact.display_name or contact.name):
                    existing.name = contact.display_name or contact.name
    return result


def _code_key(telegram_id: int) -> str:
    return f"{_CODE_PREFIX}.{telegram_id}"


def _new_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def _adam_url(magis_id: int, current_magic_id: int) -> tuple[int, str] | None:
    """Resolve the direct MAGIS Adam service, never a parent MAGIS Adam."""
    with open_magis_session() as session:
        magis = session.get(MAGIS, magis_id)
        if magis is None or magis.adam_id is None or magis.adam_id == current_magic_id:
            return None
        runtime = session.scalar(select(EveRuntime).where(EveRuntime.magic_id == magis.adam_id))
        if runtime and runtime.deployment_name and runtime.observed_state not in {"stopped", "deleted"}:
            return magis.adam_id, f"http://{runtime.deployment_name}:42069"
        root = session.scalar(select(MAGIS).where(MAGIS.parent_id.is_(None)).order_by(MAGIS.id))
        if root and root.adam_id == magis.adam_id:
            return magis.adam_id, os.environ.get("MAGI_ROOT_RUNTIME_URL", "http://magi:42069")
    return None


async def _send_code(magic_id: int, magis_id: int, telegram_id: int, text: str) -> str:
    token = state_get(require_state_dir(), "telegram.bot_token")
    if token:
        await tg_bot.send_text_raw(token, telegram_id, text)
        return "self"

    fallback = _adam_url(magis_id, magic_id)
    if fallback is None:
        raise MagiHTTPException(
            409,
            "access.no_delivery_bot",
            "This MAGI has no Bot and its direct MAGIS Adam Bot is unavailable",
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
        raise MagiHTTPException(503, "access.fallback_delivery_failed", "Adam Bot could not deliver the login code")
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
    account = _accounts(magis_id).get(payload.telegram_id)
    if account is None:
        # Do not turn this into a principal-enumeration endpoint.
        return LoginCodeResponse(ok=True, expires_in=_TTL_SECONDS)
    previous_raw = state_get(require_state_dir(), _code_key(payload.telegram_id))
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
    state_set(require_state_dir(), _code_key(payload.telegram_id), json.dumps({
        "code": code, "expires_at": now.timestamp() + _TTL_SECONDS, "last_sent_at": now.timestamp(),
    }))
    try:
        delivery = await _send_code(
            magic_id, magis_id, payload.telegram_id,
            f"Your MAGI sign-in code is: <code>{code}</code>\n\nThis code expires in 5 minutes.",
        )
    except Exception as exc:
        state_delete(require_state_dir(), _code_key(payload.telegram_id))
        if isinstance(exc, MagiHTTPException):
            raise
        raise MagiHTTPException(503, "access.delivery_failed", "Could not deliver the login code") from exc
    return LoginCodeResponse(ok=True, expires_in=_TTL_SECONDS, delivery=delivery)


@router.post("/access/verify-login-code", response_model=VerifyLoginCodeResponse)
async def verify_login_code(payload: VerifyLoginCodeRequest, request: Request) -> VerifyLoginCodeResponse:
    _require_webui(request)
    _magic_id, magis_id = _direct_magis()
    account = _accounts(magis_id).get(payload.telegram_id)
    if account is None:
        return VerifyLoginCodeResponse(ok=False, error="Code does not match")
    raw = state_get(require_state_dir(), _code_key(payload.telegram_id))
    if not raw:
        return VerifyLoginCodeResponse(ok=False, error="No code was sent — request a new one.")
    state_delete(require_state_dir(), _code_key(payload.telegram_id))
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
