"""Login / session API.

Two-step flow (mirror of admin verification):
    1. ``POST /api/auth/send-login-code { tgid }``
       Sends a 6-digit code to the tgid via the saved bot. Same
       5-min TTL and 60-s cooldown as admin code, stored under the
       same key namespace as a precaution.

    2. ``POST /api/auth/verify-login-code { tgid, code }``
       On match, sets the ``magi_session`` cookie to the
       **uid** (stringified int) and returns 200. The cookie
       is HTTPOnly + SameSite=Lax; for C0 we skip signed-cookie /
       token-store machinery (C8 hardening).

Authorization model (D.24):
  The cookie's value identifies the **employee**, not the
  channel. The login input (``tgid``) is a TG delivery
  address; the cookie output is an employee id. An employee
  can be bound to multiple channels — the cookie identity
  stays stable across all of them. ``/me`` resolves the
  cookie's employee id to the row and reports both
  ``uid`` and ``telegram_id`` (the latter may be
  ``None`` for admins who never bound a TG bot).

  Reads (list / get sessions) are scoped by ``uid``
  on the server side (see :class:`SessionStore` D.23) —
  an employee sees their own history across every channel,
  regardless of which one was used to create a given row.
  Writes (continue-send / append) are still channel-owned
  via D.22's :class:`ChannelMismatchError`: an employee can
  only continue a conversation on the channel that
  originally created it.
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
from sqlalchemy import select

# Top-level import (not lazy): ``_state_dir`` is a module-
# level helper called by every handler in this file.
# A lazy import inside ``_state_dir`` would resolve fine
# when called from inside a function — BUT the auth
# routes run inside the same FastAPI worker that imports
# this module at boot, and any other module that imports
# ``_state_dir`` (transitively, via
# ``from .auth import _state_dir`` or similar) would
# resolve the name at import time, before the function
# body ever runs. Same root cause as the D.22 fix on
# ``magi/node/__init__.py``: hoist the import.
from magi.agent.db import Employee, open_session, require_state_dir  # noqa: E402
from magi.agent.db.settings import state_get  # noqa: E402

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
    return require_state_dir()


def _super_admins() -> set[int]:
    """Read the super-admin allowlist as a set of employee ids.

    The identity is now the **employee**, not the tgid — an
    employee with a bound TG tgid is the same identity as
    that same employee signing in via WebUI. The legacy
    ``telegram.super_admins`` meta key (pre-D.24) stores
    chat_ids; the fallback path resolves each legacy
    tgid to its current ``Employee.id`` so old state
    files keep working.

    Source of truth is the ``employees`` table (rows with
    ``role='admin'``). The fallback path is retired in C8
    once no production state still carries the legacy key.
    """
    state_dir = _state_dir()
    result: set[int] = set()
    try:
        with open_session() as session:
            for emp in session.scalars(
                select(Employee).where(Employee.role == "admin")
            ).all():
                result.add(emp.id)
        if result:
            return result
    except Exception:
        # If the ORM read fails (table not initialised yet,
        # very-early-boot case) fall through to the meta.
        pass

    raw = state_get(state_dir, "telegram.super_admins")
    if not raw:
        return set()
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return set()
    if not isinstance(parsed, list):
        return set()
    # Legacy entries were telegram chat_ids (decimal digit
    # strings). Resolve each to the current Employee.id via
    # the ``telegram_id`` column so the cookie identity
    # matches the new schema.
    legacy_chat_ids: list[int] = []
    for x in parsed:
        try:
            legacy_chat_ids.append(int(x))
        except (TypeError, ValueError):
            continue
    if not legacy_chat_ids:
        return set()
    try:
        with open_session() as session:
            rows = session.scalars(
                select(Employee).where(Employee.telegram_id.in_(legacy_chat_ids))
            ).all()
            return {emp.id for emp in rows}
    except Exception:
        logger.exception("super_admins: legacy meta lookup failed")
        return set()


# -- request / response schemas -----------------------------------------


class AllowedLoginAccount(BaseModel):
    """One row in the login-page dropdown.

    ``role`` is "super_admin" for the wizard-configured deployers, and
    will grow to "assigned_employee" once C2 starts persisting
    employees with bound TG chat_ids. The frontend doesn't branch on
    the value today — it just uses it to disambiguate rows that
    happen to share a display name.

    D.24: ``uid`` is the cross-channel identity that
    will become the cookie value after the code verifies.
    The frontend keeps sending ``tgid`` on the verify
    call (the wire format is unchanged) but the cookie
    identity is the employee, so we surface the
    uid alongside for future UI work.
    """

    tgid: str
    uid: int
    display_name: str | None = None
    role: str


class AllowedLoginAccountsResponse(BaseModel):
    accounts: list[AllowedLoginAccount]


class SendLoginCodeRequest(BaseModel):
    tgid: str = Field(min_length=1, max_length=64)


class SendLoginCodeResponse(BaseModel):
    ok: bool
    expires_in: int = 0
    error: str | None = None


class VerifyLoginCodeRequest(BaseModel):
    tgid: str = Field(min_length=1, max_length=64)
    code: str = Field(min_length=6, max_length=6)


class VerifyLoginCodeResponse(BaseModel):
    ok: bool
    error: str | None = None


class MeResponse(BaseModel):
    """Response of ``/me``.

    D.24: the cookie is keyed by ``uid``, not
    tgid. The response surfaces the employee identity
    so the frontend can display it directly; the
    ``telegram_id`` is the per-channel delivery address
    bound to this employee (may be ``None`` for admins
    who never bound a TG bot) and is exposed for any
    future "send to my TG" affordance.
    """

    uid: int
    telegram_id: int | None = None
    display_name: str | None = None
    is_super_admin: bool = True  # for C0: the only kind of logged-in user


def _generate_code() -> str:
    import secrets

    return f"{secrets.randbelow(1_000_000):06d}"


# -- shared TG send + store / verify logic ------------------------------
#
# Keep this here (vs reusing the admin code in onboarding.py) because
# the auth and admin flows are conceptually different even though the
# underlying mechanism is the same: a tgid in
# telegram.super_admins is the only allowed login target, but the
# admin *verification* flow is one-shot during onboarding. If the
# login flow grows (e.g. 2FA via email later) the duplication
# becomes worth the separation. For C0 we accept the small copy.

_LOGIN_KEY = "telegram.login_code"


def _load_login_code(tgid: str) -> dict | None:
    from magi.agent.db.settings import state_get

    raw = state_get(_state_dir(), f"{_LOGIN_KEY}.{tgid}")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


def _store_login_code(tgid: str, code: str, issued_at: datetime, expires_at: float) -> None:
    from magi.agent.db.settings import state_set

    state_set(
        _state_dir(),
        f"{_LOGIN_KEY}.{tgid}",
        json.dumps(
            {
                "code": code,
                "issued_at": issued_at.replace(microsecond=0).isoformat(),
                "expires_at": expires_at,
                "last_sent_at": issued_at.timestamp(),
            }
        ),
    )


def _clear_login_code(tgid: str) -> None:
    from magi.agent.db.settings import state_delete

    state_delete(_state_dir(), f"{_LOGIN_KEY}.{tgid}")


def _employee_id_for_chat_id(tgid: str) -> int | None:
    """Resolve a TG tgid (the login input) to its employee id.

    Returns ``None`` if the tgid isn't bound to any
    ``Employee`` row. The login flow uses this to translate
    "what the user typed" into the cookie identity; the
    cookie value is the uid (D.24) so a future
    channel (Slack / WeChat) can resolve its own address
    to the same identity.
    """
    try:
        cid_int = int(tgid)
    except (TypeError, ValueError):
        return None
    try:
        with open_session() as session:
            emp = session.scalar(
                select(Employee).where(Employee.telegram_id == cid_int)
            )
            return emp.id if emp is not None else None
    except Exception:
        logger.exception("login: telegram_id → employee lookup failed")
        return None


# -- endpoints ---------------------------------------------------------


@router.get("/allowed-chat-ids", response_model=AllowedLoginAccountsResponse)
async def list_allowed_chat_ids() -> AllowedLoginAccountsResponse:
    """The list of chat_ids that can log in to Adam.

    Two sources, unioned:

    1. ``telegram.super_admins`` — the wizard-configured deployer
       list. role = ``super_admin``.
    2. Employees with a bound TG tgid + an active EVE
       assignment. role = ``assigned_employee``. C2 wires the TG
       binding (the employee proves ownership of the chat from
       TG by replying to a code); C6 wires the EVE dispatch
       (Adam spawns a container for the employee). The two
       together mean "this person has a live EVE they manage" —
       they should be able to sign in to see its logs, change
       its skills, etc., without needing deployer-level access.

    For C0 the employees side is empty (the tables don't exist
    yet — C1.1 lands the ORM). The path is wired so the
    frontend can show "0 assigned employees" today and start
    populating as soon as C6 dispatches the first EVE.

    Display names are best-effort lookups via Telegram
    ``getChat``; a failure (e.g. the user has blocked the bot,
    or the network is down) just means we fall back to showing
    the bare tgid.
    """
    from magi.agent.db.settings import state_get

    bot_token = state_get(_state_dir(), "telegram.bot_token")
    accounts: list[AllowedLoginAccount] = []

    # 1. Super admins (wizard-configured). The list now
    # resolves employee ids → their bound TG chat_ids so
    # the dropdown still shows the tgid the user
    # actually types on the login page. Admins without a
    # TG binding can't be reached via the bot today; the
    # ``/allowed-chat-ids`` response drops them rather
    # than rendering an un-usable row.
    admin_employee_ids = _super_admins()
    admin_telegram_ids: dict[int, int] = {}
    if admin_employee_ids:
        with open_session() as session:
            for emp in session.scalars(
                select(Employee).where(Employee.id.in_(admin_employee_ids))
            ).all():
                if emp.telegram_id is not None:
                    admin_telegram_ids[emp.id] = emp.telegram_id

    for uid in sorted(admin_telegram_ids):
        chat_id_int = admin_telegram_ids[uid]
        tgid = str(chat_id_int)
        display_name: str | None = None
        if bot_token:
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    resp = await client.post(
                        f"https://api.telegram.org/bot{bot_token}/getChat",
                        json={"tgid": chat_id_int},
                    )
                if resp.status_code == 200 and (resp.json().get("ok")):
                    chat = resp.json().get("result") or {}
                    display_name = (
                        chat.get("first_name")
                        or chat.get("title")
                        or chat.get("username")
                    )
            except (httpx.TimeoutException, httpx.RequestError, ValueError):
                # Network down / user blocked the bot / whatever —
                # the dropdown will just show the tgid.
                pass
        accounts.append(
            AllowedLoginAccount(
                tgid=tgid,
                uid=uid,
                display_name=display_name,
                role="super_admin",
            )
        )

    # 2. Assigned employees. C0: the employees / eves tables don't
    # exist yet, so this list is always empty. The query is
    # sketched in the comment so C1.1 / C6 can drop it in.
    #
    #   SELECT e.telegram_id, e.name
    #   FROM employees e
    #   JOIN eves v ON v.uid = e.id
    #   WHERE e.telegram_id IS NOT NULL
    #     AND v.status != 'shutting_down'
    #     AND NOT EXISTS (
    #       SELECT 1 FROM telegram.super_admins a
    #       WHERE a.tgid = e.telegram_id
    #     );
    #
    # We skip the join and rely on the frontend to de-dupe
    # super_admins from the visible list (super admins should
    # not also appear under "assigned employees" — they manage
    # the system, not a single EVE).

    return AllowedLoginAccountsResponse(accounts=accounts)


@router.post("/send-login-code", response_model=SendLoginCodeResponse)
async def send_login_code(
    payload: SendLoginCodeRequest,
) -> SendLoginCodeResponse:
    """Send a 6-digit code to ``tgid`` for login.

    No-op if the tgid isn't bound to a super-admin employee —
    we don't want random TG users to be able to spam a
    magic-link code into the bot. The user-facing error in
    that case is identical to a successful send
    (anti-enumeration), but the bot never sends a message
    and no code is stored.

    D.24: the input is still a TG tgid (what the user
    types), but the allowlist is now the **uid**.
    We resolve the tgid → uid once at the top,
    then key the cooldown / code store by uid so
    the lookup is independent of which channel the user
    used to log in (a future Slack / IM channel will
    resolve its own address to the same uid).
    """
    tgid = payload.tgid.strip()
    if not tgid.lstrip("-").isdigit():
        return SendLoginCodeResponse(ok=False, error="tgid must be numeric")

    # Resolve tgid → uid. The super-admin
    # allowlist is keyed by uid; if the tgid
    # doesn't resolve to an admin employee, no code goes
    # out.
    uid = _employee_id_for_chat_id(tgid)
    if uid is None or uid not in _super_admins():
        # Anti-enumeration: respond as if we sent, but no-op
        # behind the scenes. The frontend shows the same
        # "code sent" UX so an attacker can't probe which
        # chat_ids are admins.
        logger.info(
            "login-code send: tgid is not a super_admin; suppressed",
            extra={"tgid": tgid},
        )
        return SendLoginCodeResponse(ok=True, expires_in=_CODE_TTL_SECONDS)

    # Cooldown — same anti-abuse logic as the admin code.
    previous = _load_login_code(tgid)
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

    from magi.agent.db.settings import state_get

    bot_token = state_get(_state_dir(), "telegram.bot_token")
    if not bot_token:
        return SendLoginCodeResponse(
            ok=False,
            error="Bot token not configured yet — can't send login code.",
        )

    code = _generate_code()
    issued_at = datetime.now(timezone.utc)
    expires_at = issued_at.timestamp() + _CODE_TTL_SECONDS
    _store_login_code(tgid, code, issued_at, expires_at)

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                url,
                json={
                    "tgid": int(tgid),
                    "text": (
                        f"Your MAGI sign-in code is: <code>{code}</code>\n\n"
                        f"Enter it in the browser to log in. The code "
                        f"expires in {_CODE_TTL_SECONDS // 60} minutes."
                    ),
                },
            )
    except httpx.TimeoutException:
        _clear_login_code(tgid)
        return SendLoginCodeResponse(ok=False, error="Telegram timed out")
    except httpx.RequestError as exc:
        _clear_login_code(tgid)
        return SendLoginCodeResponse(ok=False, error=f"Network error: {exc}")

    if resp.status_code != 200:
        _clear_login_code(tgid)
        return SendLoginCodeResponse(
            ok=False,
            error=f"Telegram returned HTTP {resp.status_code}",
        )

    data = resp.json()
    if not data.get("ok"):
        _clear_login_code(tgid)
        return SendLoginCodeResponse(
            ok=False, error=data.get("description", "Telegram error")
        )

    return SendLoginCodeResponse(ok=True, expires_in=_CODE_TTL_SECONDS)


@router.post("/verify-login-code", response_model=VerifyLoginCodeResponse)
async def verify_login_code(
    payload: VerifyLoginCodeRequest,
    response: Response,
) -> VerifyLoginCodeResponse:
    """Check the code, then set the session cookie on success.

    D.24: the cookie value is the **uid**, not the
    tgid. The tgid is still on the wire (it's what
    the user typed and what the bot delivered the code
    to) but the cookie identity is the cross-channel
    employee — so signing in via WebUI works whether the
    admin's only channel is the bot, or a future Slack,
    or no chat channel at all (an admin who manages
    other employees but never bound a TG bot).
    """
    tgid = payload.tgid.strip()
    code = payload.code.strip()
    if not code.isdigit() or len(code) != 6:
        return VerifyLoginCodeResponse(ok=False, error="Code must be 6 digits")

    uid = _employee_id_for_chat_id(tgid)
    if uid is None or uid not in _super_admins():
        # Anti-enumeration: same response as a wrong code.
        return VerifyLoginCodeResponse(ok=False, error="Code does not match")

    stored = _load_login_code(tgid)
    if not stored:
        return VerifyLoginCodeResponse(
            ok=False,
            error="No code sent to this tgid — request a new one.",
        )

    try:
        expires_at = float(stored.get("expires_at", 0))
    except (TypeError, ValueError):
        expires_at = 0
    if not expires_at or datetime.now(timezone.utc).timestamp() >= expires_at:
        _clear_login_code(tgid)
        return VerifyLoginCodeResponse(
            ok=False, error="Code expired — request a new one."
        )

    # Burn on any path past expiry (mismatch, success, anything) so
    # the code can't be retried.
    _clear_login_code(tgid)

    if str(stored.get("code", "")) != code:
        return VerifyLoginCodeResponse(ok=False, error="Code does not match")

    # Sign in: set the session cookie. D.24 — the cookie
    # value is the uid (cross-channel identity),
    # not the tgid. The HTTPOnly flag keeps it
    # client-side inaccessible; the ``/me`` endpoint and
    # every AdminGate lookup resolve it back to a live
    # ``Employee`` row to gate access. C8 will replace
    # this with a signed token + a real session table.
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=str(uid),
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        # path="/" so every endpoint sees it
        path="/",
    )
    logger.info(
        "user signed in",
        extra={"tgid": tgid, "uid": uid},
    )
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

    "Valid" means: the cookie's uid resolves to a
    row in ``employees`` with role='admin'. D.24 — the
    cookie is the uid (cross-channel identity),
    not the tgid the user typed on the login page.
    The ``telegram_id`` field in the response is the
    bound TG chat id, looked up from the same row.
    """
    from magi.channels.webui.api.errors import MagiHTTPException

    if not magi_session or not magi_session.isdigit():
        raise MagiHTTPException(
            status_code=401, code="auth.not_signed_in", detail="Not signed in"
        )
    uid = int(magi_session)
    if uid not in _super_admins():
        raise MagiHTTPException(
            status_code=401, code="auth.not_signed_in", detail="Not signed in"
        )
    # We already proved the cookie is a valid admin
    # uid; re-read the row to surface the
    # telegram_id + display name. If the row has since
    # been deleted (admin removed mid-session), we fall
    # back to None — the cookie is still good for this
    # request, just no metadata to enrich the response
    # with.
    try:
        with open_session() as session:
            emp = session.get(Employee, uid)
        if emp is None:
            return MeResponse(
                uid=uid,
                telegram_id=None,
                display_name=None,
            )
        return MeResponse(
            uid=emp.id,
            telegram_id=emp.telegram_id,
            display_name=emp.name,
        )
    except Exception:
        logger.exception("me: employee lookup failed for cookie value")
        return MeResponse(uid=uid)