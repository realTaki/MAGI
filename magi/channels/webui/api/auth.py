"""Login / session API.

Two-step flow (mirror of admin verification):
    1. ``POST /api/auth/send-login-code { chat_id }``
       Sends a 6-digit code to the chat_id via the saved bot. Same
       5-min TTL and 60-s cooldown as admin code, stored under the
       same key namespace as a precaution.

    2. ``POST /api/auth/verify-login-code { chat_id, code }``
       On match, sets the ``magi_session`` cookie to the chat_id and
       returns 200. The cookie is HTTPOnly + SameSite=Lax; for C0 we
       skip signed-cookie / token-store machinery (C8 hardening).

Authorization: ``GET /api/auth/me`` returns 200 + user info if the
cookie's chat_id is in ``telegram.super_admins``; 401 otherwise. The
``is_super_admin`` check is the only thing this endpoint relies on —
we don't keep a separate users table, so "logging out" is just
clearing the cookie (``POST /api/auth/logout``).
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Annotated

import httpx
from fastapi import APIRouter, Cookie, Response
from pydantic import BaseModel, Field

logger = logging.getLogger("magi.api.auth")

router = APIRouter(tags=["auth"])

# Same TTL / cooldown as the admin code — reuses the user's mental
# model. Could be tuned later; for now identical is fine.
_CODE_TTL_SECONDS = 300
_RESEND_COOLDOWN_SECONDS = 60

SESSION_COOKIE_NAME = "magi_session"
# 14 days is long enough to be useful (the deployer doesn't sign in
# every day) and short enough that an idle laptop eventually kicks
# them out. C8 will add rotation.
SESSION_TTL_SECONDS = 14 * 24 * 60 * 60


def _state_dir() -> str:
    return os.environ.get("MAGI_STATE_DIR", "/workspace/state")


def _super_admins() -> set[str]:
    """Read telegram.super_admins as a set of chat_id strings."""
    from magi.runtime.state.settings import state_get

    raw = state_get(_state_dir(), "telegram.super_admins")
    if not raw:
        return set()
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return set()
    if not isinstance(parsed, list):
        return set()
    return {str(x) for x in parsed}


# -- request / response schemas -----------------------------------------


class SendLoginCodeRequest(BaseModel):
    chat_id: str = Field(min_length=1, max_length=64)


class SendLoginCodeResponse(BaseModel):
    ok: bool
    expires_in: int = 0
    error: str | None = None


class VerifyLoginCodeRequest(BaseModel):
    chat_id: str = Field(min_length=1, max_length=64)
    code: str = Field(min_length=6, max_length=6)


class VerifyLoginCodeResponse(BaseModel):
    ok: bool
    error: str | None = None


class MeResponse(BaseModel):
    chat_id: str
    display_name: str | None = None
    is_super_admin: bool = True  # for C0: the only kind of logged-in user


def _generate_code() -> str:
    import secrets

    return f"{secrets.randbelow(1_000_000):06d}"


# -- shared TG send + store / verify logic ------------------------------
#
# Keep this here (vs reusing the admin code in onboarding.py) because
# the auth and admin flows are conceptually different even though the
# underlying mechanism is the same: a chat_id in
# telegram.super_admins is the only allowed login target, but the
# admin *verification* flow is one-shot during onboarding. If the
# login flow grows (e.g. 2FA via email later) the duplication
# becomes worth the separation. For C0 we accept the small copy.

_LOGIN_KEY = "telegram.login_code"


def _load_login_code(chat_id: str) -> dict | None:
    from magi.runtime.state.settings import state_get

    raw = state_get(_state_dir(), f"{_LOGIN_KEY}.{chat_id}")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


def _store_login_code(chat_id: str, code: str, issued_at: datetime, expires_at: float) -> None:
    from magi.runtime.state.settings import state_set

    state_set(
        _state_dir(),
        f"{_LOGIN_KEY}.{chat_id}",
        json.dumps(
            {
                "code": code,
                "issued_at": issued_at.replace(microsecond=0).isoformat(),
                "expires_at": expires_at,
                "last_sent_at": issued_at.timestamp(),
            }
        ),
    )


def _clear_login_code(chat_id: str) -> None:
    from magi.runtime.state.settings import state_delete

    state_delete(_state_dir(), f"{_LOGIN_KEY}.{chat_id}")


# -- endpoints ---------------------------------------------------------


@router.post("/send-login-code", response_model=SendLoginCodeResponse)
async def send_login_code(
    payload: SendLoginCodeRequest,
) -> SendLoginCodeResponse:
    """Send a 6-digit code to ``chat_id`` for login.

    No-op if the chat_id isn't in the super-admins list — we don't
    want random TG users to be able to spam a magic-link code into
    the bot. The user-facing error in that case is identical to a
    successful send (anti-enumeration), but the bot never sends a
    message and no code is stored.
    """
    chat_id = payload.chat_id.strip()
    if not chat_id.lstrip("-").isdigit():
        return SendLoginCodeResponse(ok=False, error="chat_id must be numeric")

    if chat_id not in _super_admins():
        # Anti-enumeration: respond as if we sent, but no-op behind
        # the scenes. The frontend shows the same "code sent" UX so
        # an attacker can't probe which chat_ids are admins.
        logger.info(
            "login-code send: chat_id is not a super_admin; suppressed",
            extra={"chat_id": chat_id},
        )
        return SendLoginCodeResponse(ok=True, expires_in=_CODE_TTL_SECONDS)

    # Cooldown — same anti-abuse logic as the admin code.
    previous = _load_login_code(chat_id)
    if previous:
        try:
            prev_sent_at = float(previous.get("last_sent_at", 0))
        except (TypeError, ValueError):
            prev_sent_at = 0
        if prev_sent_at:
            elapsed = datetime.now(timezone.utc).timestamp() - prev_sent_at
            if elapsed < _RESEND_COOLDOWN_SECONDS:
                remaining = int(_RESEND_COOLDOWN_SECONDS - elapsed)
                return SendLoginCodeResponse(
                    ok=False,
                    error=f"Wait {remaining}s before requesting a new code.",
                )

    from magi.runtime.state.settings import state_get

    bot_token = state_get(_state_dir(), "telegram.bot_token")
    if not bot_token:
        return SendLoginCodeResponse(
            ok=False,
            error="Bot token not configured yet — can't send login code.",
        )

    code = _generate_code()
    issued_at = datetime.now(timezone.utc)
    expires_at = issued_at.timestamp() + _CODE_TTL_SECONDS
    _store_login_code(chat_id, code, issued_at, expires_at)

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                url,
                json={
                    "chat_id": int(chat_id),
                    "text": (
                        f"Your MAGI sign-in code is: <code>{code}</code>\n\n"
                        f"Enter it in the browser to log in. The code "
                        f"expires in {_CODE_TTL_SECONDS // 60} minutes."
                    ),
                },
            )
    except httpx.TimeoutException:
        _clear_login_code(chat_id)
        return SendLoginCodeResponse(ok=False, error="Telegram timed out")
    except httpx.RequestError as exc:
        _clear_login_code(chat_id)
        return SendLoginCodeResponse(ok=False, error=f"Network error: {exc}")

    if resp.status_code != 200:
        _clear_login_code(chat_id)
        return SendLoginCodeResponse(
            ok=False,
            error=f"Telegram returned HTTP {resp.status_code}",
        )

    data = resp.json()
    if not data.get("ok"):
        _clear_login_code(chat_id)
        return SendLoginCodeResponse(
            ok=False, error=data.get("description", "Telegram error")
        )

    return SendLoginCodeResponse(ok=True, expires_in=_CODE_TTL_SECONDS)


@router.post("/verify-login-code", response_model=VerifyLoginCodeResponse)
async def verify_login_code(
    payload: VerifyLoginCodeRequest,
    response: Response,
) -> VerifyLoginCodeResponse:
    """Check the code, then set the session cookie on success."""
    chat_id = payload.chat_id.strip()
    code = payload.code.strip()
    if not code.isdigit() or len(code) != 6:
        return VerifyLoginCodeResponse(ok=False, error="Code must be 6 digits")

    if chat_id not in _super_admins():
        # Anti-enumeration: same response as a wrong code.
        return VerifyLoginCodeResponse(ok=False, error="Code does not match")

    stored = _load_login_code(chat_id)
    if not stored:
        return VerifyLoginCodeResponse(
            ok=False,
            error="No code sent to this chat_id — request a new one.",
        )

    try:
        expires_at = float(stored.get("expires_at", 0))
    except (TypeError, ValueError):
        expires_at = 0
    if not expires_at or datetime.now(timezone.utc).timestamp() >= expires_at:
        _clear_login_code(chat_id)
        return VerifyLoginCodeResponse(
            ok=False, error="Code expired — request a new one."
        )

    # Burn on any path past expiry (mismatch, success, anything) so
    # the code can't be retried.
    _clear_login_code(chat_id)

    if str(stored.get("code", "")) != code:
        return VerifyLoginCodeResponse(ok=False, error="Code does not match")

    # Sign in: set the session cookie. For C0 the cookie value IS
    # the chat_id — we trust the HTTPOnly flag to keep it client-side
    # inaccessible, and the /me endpoint verifies the value is still
    # in the super-admins list. C8 will replace with a signed token +
    # a real session table.
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=chat_id,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        # path="/" so every endpoint sees it
        path="/",
    )
    logger.info("user signed in", extra={"chat_id": chat_id})
    return VerifyLoginCodeResponse(ok=True)


@router.post("/logout", status_code=204)
async def logout(response: Response) -> Response:
    """Clear the session cookie. The endpoint always returns 204 even
    if the user wasn't signed in — logout is idempotent."""
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return Response(status_code=204)


@router.get("/me", response_model=MeResponse)
async def me(
    magi_session: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None,
) -> MeResponse:
    """Return the current user, or 401 if no valid session.

    "Valid" means: the cookie's chat_id is in ``telegram.super_admins``.
    We don't bother fetching the display name here — the dashboard can
    do that lazily once it knows the user is signed in.
    """
    from fastapi import HTTPException

    if not magi_session or magi_session not in _super_admins():
        raise HTTPException(status_code=401, detail="Not signed in")
    return MeResponse(chat_id=magi_session, display_name=None)