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
    tgids; the fallback path resolves each legacy
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
    # Legacy entries were telegram tgids (decimal digit
    # strings). Resolve each to the current Employee.id via
    # the ``telegram_id`` column so the cookie identity
    # matches the new schema.
    legacy_tgids: list[int] = []
    for x in parsed:
        try:
            legacy_tgids.append(int(x))
        except (TypeError, ValueError):
            continue
    if not legacy_tgids:
        return set()
    try:
        with open_session() as session:
            rows = session.scalars(
                select(Employee).where(Employee.telegram_id.in_(legacy_tgids))
            ).all()
            return {emp.id for emp in rows}
    except Exception:
        logger.exception("super_admins: legacy meta lookup failed")
        return set()


# -- request / response schemas -----------------------------------------


class AllowedLoginAccount(BaseModel):
    """One row in the login-page dropdown.

    ``uid`` is the cross-channel identity; the cookie value
    after a successful verify is the uid. ``telegram_id``
    is the bound IM channel used to deliver the 6-digit
    code; multiple IM channels per UID (Slack, etc.) will
    extend this row to a list as those channels land.

    Only admin rows with at least one bound IM are listed;
    an admin with no IM binding can't receive the login
    code so they have no login affordance today.

    ``role`` disambiguates rows that share a display name
    (e.g. two employees both called "Alice").
    """

    uid: int
    telegram_id: int | None = None
    display_name: str | None = None
    role: str


class AllowedLoginAccountsResponse(BaseModel):
    accounts: list[AllowedLoginAccount]


class SendLoginCodeRequest(BaseModel):
    # UID is the cross-channel identity; the server
    # resolves the UID's bound IM (TG for now) and
    # delivers the code through it. The wire format is
    # intentionally uid-only so adding Slack / future IMs
    # is purely a server-side change.
    uid: int


class SendLoginCodeResponse(BaseModel):
    ok: bool
    expires_in: int = 0
    error: str | None = None


class VerifyLoginCodeRequest(BaseModel):
    uid: int
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
# The login flow is UID-centric: the public input is the user's
# UID (the cookie identity), and the server resolves that UID's
# bound IM channel (TG today; Slack / WeChat tomorrow) at
# code-send time. The cooldown + code store key off the UID, not
# the IM address — that way a future second-IM binding doesn't
# leave half-stored codes behind.
#
# The TG client API uses a vendor-fixed kwarg name on the
# contract), but that's an internal detail of the
# ``_send_via_telegram`` helper — callers never see a tgid.

_LOGIN_KEY = "auth.login_code"


def _load_login_code(uid: int) -> dict | None:
    from magi.agent.db.settings import state_get

    raw = state_get(_state_dir(), f"{_LOGIN_KEY}.{uid}")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


def _store_login_code(uid: int, code: str, issued_at: datetime, expires_at: float) -> None:
    from magi.agent.db.settings import state_set

    state_set(
        _state_dir(),
        f"{_LOGIN_KEY}.{uid}",
        json.dumps(
            {
                "code": code,
                "issued_at": issued_at.replace(microsecond=0).isoformat(),
                "expires_at": expires_at,
                "last_sent_at": issued_at.timestamp(),
            }
        ),
    )


def _clear_login_code(uid: int) -> None:
    from magi.agent.db.settings import state_delete

    state_delete(_state_dir(), f"{_LOGIN_KEY}.{uid}")


# -- endpoints ---------------------------------------------------------


@router.get("/allowed-accounts", response_model=AllowedLoginAccountsResponse)
async def list_allowed_accounts() -> AllowedLoginAccountsResponse:
    """The list of UIDs that can log in to Adam.

    UID is the row's identity; the dropdown's primary key is
    the UID, not the IM chat id. We still include
    ``telegram_id`` in the response so the frontend can show
    "Alice (Telegram: 9999001)" — useful when two employees
    share a display name — but the wire-protocol ask for the
    verification code takes the UID, not the tgid.

    Filter: admin rows that have at least one bound IM
    (today: ``telegram_id IS NOT NULL``). An admin who never
    bound a TG chat can't receive the verification code
    so they have no login affordance; the row is dropped
    rather than greyed out so the dropdown stays small.

    Two sources, unioned:

    1. ``role='admin'`` rows in ``employees`` — the
       wizard-configured deployer list. role =
       ``super_admin``.
    2. Employees with a bound TG tgid + an active EVE
       assignment. role = ``assigned_employee``. C2 wires
       the TG binding (the employee proves ownership of the
       chat from TG by replying to a code); C6 wires the
       EVE dispatch (Adam spawns a container for the
       employee). The two together mean "this person has a
       live EVE they manage" — they should be able to sign
       in to see its logs, change its skills, etc., without
       needing deployer-level access.

    For C0 the employees side is empty (the tables don't
    exist yet — C1.1 lands the ORM). The path is wired so
    the frontend can show "0 assigned employees" today and
    start populating as soon as C6 dispatches the first EVE.

    Display names are best-effort lookups via Telegram
    ``getChat``; a failure (e.g. the user has blocked the
    bot, or the network is down) just means we fall back to
    showing the bare uid.
    """
    from magi.agent.db.settings import state_get

    bot_token = state_get(_state_dir(), "telegram.bot_token")
    accounts: list[AllowedLoginAccount] = []

    # 1. Super admins (wizard-configured). We resolve
    # each uid → its bound TG telegram_id so the
    # frontend can show the chat hint next to the
    # display name. Admins without a TG binding are
    # dropped (no IM to deliver the verification code
    # through).
    admin_uids = _super_admins()
    admin_rows: list[tuple[int, int, str | None]] = []  # (uid, telegram_id, name)
    if admin_uids:
        with open_session() as session:
            for emp in session.scalars(
                select(Employee).where(Employee.id.in_(admin_uids))
            ).all():
                if emp.telegram_id is not None:
                    admin_rows.append(
                        (emp.id, emp.telegram_id, emp.display_name or emp.name)
                    )

    for uid, telegram_id_int, fallback_name in sorted(admin_rows):
        display_name: str | None = None
        if bot_token:
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    resp = await client.post(
                        f"https://api.telegram.org/bot{bot_token}/getChat",
                        json={"tgid": telegram_id_int},
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
                # the dropdown falls back to the Employee row's
                # cached display name.
                pass
        accounts.append(
            AllowedLoginAccount(
                uid=uid,
                telegram_id=telegram_id_int,
                display_name=display_name or fallback_name,
                role="super_admin",
            )
        )

    # 2. Assigned employees. C0: the employees / eves tables don't
    # exist yet, so this list is always empty. The query is
    # sketched in the comment so C1.1 / C6 can drop it in.
    #
    #   SELECT e.uid, e.telegram_id, e.name
    #   FROM employees e
    #   JOIN eves v ON v.uid = e.id
    #   WHERE e.telegram_id IS NOT NULL
    #     AND v.status != 'shutting_down'
    #     AND NOT EXISTS (
    #       SELECT 1 FROM employees a
    #       WHERE a.uid = e.uid AND a.role = 'admin'
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
    """Send a 6-digit code to ``uid`` for login.

    The input is the user's UID (the cross-channel identity).
    The server resolves the UID's bound IM (TG today; Slack
    / WeChat tomorrow) at this point and delivers the code
    through whichever channel is online.

    No-op if the UID isn't an admin or has no bound IM —
    we don't want random TG users to be able to spam a
    magic-link code into the bot. The user-facing error
    in that case is identical to a successful send
    (anti-enumeration), but the bot never sends a message
    and no code is stored.
    """
    uid = payload.uid

    if uid not in _super_admins():
        # Anti-enumeration: respond as if we sent, but no-op
        # behind the scenes. The frontend shows the same
        # "code sent" UX so an attacker can't probe which
        # uids are admins.
        logger.info(
            "login-code send: uid is not a super_admin; suppressed",
            extra={"uid": uid},
        )
        return SendLoginCodeResponse(ok=True, expires_in=_CODE_TTL_SECONDS)

    # Resolve the UID's bound IM channel via the channel
    # dispatcher (D.28). Today: TG. Future: dispatcher picks
    # the first channel with a live bot. If no IM is
    # bound, refuse — we have no way to deliver the code.
    from magi.channels import dispatcher as channel_dispatcher
    if channel_dispatcher.lookup_im_id(uid, "tg") is None:
        return SendLoginCodeResponse(
            ok=False,
            error="This account has no IM channel configured; "
                  "ask the deployer to bind a TG chat first.",
        )

    # Cooldown — same anti-abuse logic as the admin code.
    # Storage is keyed by UID so a future second-IM
    # binding doesn't leave half-stored codes behind.
    previous = _load_login_code(uid)
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

    code = _generate_code()
    issued_at = datetime.now(timezone.utc)
    expires_at = issued_at.timestamp() + _CODE_TTL_SECONDS
    _store_login_code(uid, code, issued_at, expires_at)

    # Send through the resolved IM. The dispatcher routes
    # to the right channel adapter; the TG adapter does its
    # own httpx / bot call against api.telegram.org. We
    # pass the UID; the adapter looks up the bound chat id
    # itself. No more bot token + httpx here — that path
    # is encapsulated behind the adapter boundary (D.28).
    code_text = (
        f"Your MAGI sign-in code is: <code>{code}</code>\n\n"
        f"Enter it in the browser to log in. The code "
        f"expires in {_CODE_TTL_SECONDS // 60} minutes."
    )
    try:
        await channel_dispatcher.send_to_uid(uid, "tg", code_text)
    except RuntimeError as e:
        # The dispatcher raised because the user has no
        # binding or the channel's bot is offline. The
        # pre-call ``lookup_im_id`` already guards the
        # binding case, so any RuntimeError here is the
        # bot-offline case.
        _clear_login_code(uid)
        return SendLoginCodeResponse(ok=False, error=str(e))
    except Exception as e:
        _clear_login_code(uid)
        return SendLoginCodeResponse(ok=False, error=f"send failed: {e}")

    return SendLoginCodeResponse(ok=True, expires_in=_CODE_TTL_SECONDS)


@router.post("/verify-login-code", response_model=VerifyLoginCodeResponse)
async def verify_login_code(
    payload: VerifyLoginCodeRequest,
    response: Response,
) -> VerifyLoginCodeResponse:
    """Check the code, then set the session cookie on success.

    The cookie value is the UID (the cross-channel identity).
    The IM channel that delivered the code is irrelevant to
    the cookie — a future second-IM binding or no-IM
    admin who gets their code via email all set the same
    cookie (uid) on success.
    """
    uid = payload.uid
    code = payload.code.strip()
    if not code.isdigit() or len(code) != 6:
        return VerifyLoginCodeResponse(ok=False, error="Code must be 6 digits")

    if uid not in _super_admins():
        # Anti-enumeration: same response as a wrong code.
        return VerifyLoginCodeResponse(ok=False, error="Code does not match")

    stored = _load_login_code(uid)
    if not stored:
        return VerifyLoginCodeResponse(
            ok=False,
            error="No code sent to this uid \u2014 request a new one.",
        )

    try:
        expires_at = float(stored.get("expires_at", 0))
    except (TypeError, ValueError):
        expires_at = 0
    if not expires_at or datetime.now(timezone.utc).timestamp() >= expires_at:
        _clear_login_code(uid)
        return VerifyLoginCodeResponse(
            ok=False, error="Code expired \u2014 request a new one."
        )

    # Burn on any path past expiry (mismatch, success, anything) so
    # the code can't be retried.
    _clear_login_code(uid)

    if str(stored.get("code", "")) != code:
        return VerifyLoginCodeResponse(ok=False, error="Code does not match")

    # Sign in: set the session cookie. Cookie value is
    # the UID; the HTTPOnly flag keeps it client-side
    # inaccessible; the ``/me`` endpoint and every
    # AdminGate lookup resolve it back to a live
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
        extra={"uid": uid},
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