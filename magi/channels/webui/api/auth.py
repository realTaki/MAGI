"""Login / session API.

Two-step flow (mirror of admin verification):
    1. ``POST /api/auth/send-login-code { uid }``
       Sends a 6-digit code to the operator's bound TG chat via
       the saved bot. Same 5-min TTL and 60-s cooldown as admin
       code, stored under the same key namespace as a precaution.

    2. ``POST /api/auth/verify-login-code { uid, code }``
       On match, sets the ``magi_session`` cookie to the
       **uid** (stringified int) and returns 200. The cookie
       is HTTPOnly + SameSite=Lax; for C0 we skip signed-cookie /
       token-store machinery (C8 hardening).

Authorization model (D.24):
  The cookie's value identifies the **contact**, not the
  channel. The login input (``uid``) is a per-person
  identity; the cookie output is also the uid. The
  per-channel delivery address (TG chat id for admins
  who bound one) is resolved server-side via the channel
  dispatcher (D.28). A contact can be bound to
  multiple channels — the cookie identity stays stable
  across all of them. ``/me`` resolves the cookie's
  contact id to the row and reports both ``uid`` and
  ``telegram_id`` (the latter may be ``None`` for admins
  who never bound a TG bot).

  Reads (list / get sessions) are scoped by ``uid``
  on the server side (see :class:`SessionStore` D.23) —
  a contact sees their own history across every channel,
  regardless of which one was used to create a given row.
  Writes (continue-send / append) are still channel-owned
  via D.22's :class:`ChannelMismatchError`: a contact can
  only continue a conversation on the channel that
  originally created it.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Annotated

import hashlib
import hmac
import asyncio
from base64 import urlsafe_b64encode, urlsafe_b64decode
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
from magi.db import Contact, ControlOperator, EveRuntime, MAGIC, MAGIS, open_session, require_state_dir  # noqa: E402
from magi.db.settings import state_get  # noqa: E402
from magi.channels.webui import control_store
from magi.channels import Channel
from magi.channels.telegram import bot as tg_bot  # noqa: E402
from magi.channels.webui.api.errors import MagiHTTPException
from magi.channels.webui.proxy_auth import build_proxy_headers

logger = logging.getLogger("magi.api.auth")

router = APIRouter(tags=["auth"])


# Same TTL / cooldown as the admin code — reuses the user's mental
# model. Could be tuned later; for now identical is fine.
_CODE_TTL_SECONDS = 300
_RESEND_COOLDOWN_SECONDS = 60

SESSION_COOKIE_NAME = "magi_session"
SESSION_TTL_SECONDS = 14 * 24 * 60 * 60


def _signing_key() -> bytes:
    """Derive a signing key from the state directory path.
    Not cryptographically random, but prevents casual cookie
    tampering — an attacker with filesystem access to the
    state dir already owns the DB anyway."""
    if control_store.enabled():
        secret = os.environ.get("MAGI_CONTROL_SECRET")
        if secret:
            return hashlib.sha256(secret.encode() + b"magi-control-session").digest()
    return hashlib.sha256(
        _state_dir().encode() + b"magi-session-signing"
    ).digest()


def _sign_uid(uid: int) -> str:
    """Return ``uid:timestamp:hmac`` signed token."""
    ts = str(int(datetime.now(timezone.utc).timestamp()))
    payload = f"{uid}:{ts}".encode()
    sig = hmac.new(_signing_key(), payload, hashlib.sha256).hexdigest()[:16]
    token = f"{uid}:{ts}:{sig}".encode()
    return urlsafe_b64encode(token).decode().rstrip("=")


def _verify_signed_uid(token: str) -> int | None:
    """Return ``uid`` if the token is valid and not expired, else ``None``."""
    try:
        # Add padding back for base64 decode
        padded = token + "=" * (4 - len(token) % 4)
        decoded = urlsafe_b64decode(padded).decode()
        parts = decoded.split(":")
        if len(parts) != 3:
            return None
        uid_str, ts_str, sig = parts
        uid = int(uid_str)
        ts = int(ts_str)
        # Check expiry
        if datetime.now(timezone.utc).timestamp() - ts > SESSION_TTL_SECONDS:
            return None
        # Verify signature
        payload = f"{uid}:{ts}".encode()
        expected = hmac.new(_signing_key(), payload, hashlib.sha256).hexdigest()[:16]
        if not hmac.compare_digest(sig, expected):
            return None
        return uid
    except Exception:
        return None


def _sign_selected_session(*, magic_id: int, telegram_id: int, display_name: str | None, admin: bool, assigned: bool) -> str:
    """Sign a browser session bound to exactly one selected MAGI.

    ``_sign_uid`` remains for compatibility with private-runtime tests and
    old single-node sessions. New control-plane sessions are versioned and
    include their selected target, so a browser cannot reuse a B session to
    proxy requests to C.
    """
    payload = {
        "v": 2, "magic_id": magic_id, "telegram_id": telegram_id,
        "display_name": display_name, "admin": admin, "assigned": assigned,
        "ts": int(datetime.now(timezone.utc).timestamp()),
    }
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
    signature = hmac.new(_signing_key(), raw, hashlib.sha256).hexdigest()[:24]
    return "v2." + urlsafe_b64encode(raw).decode().rstrip("=") + "." + signature


def selected_session(token: str | None) -> dict[str, object] | None:
    """Return the selected-MAGI browser session, if valid and unexpired."""
    if not token or not token.startswith("v2."):
        return None
    try:
        _version, body, signature = token.split(".", 2)
        raw = urlsafe_b64decode(body + "=" * (-len(body) % 4))
        expected = hmac.new(_signing_key(), raw, hashlib.sha256).hexdigest()[:24]
        if not hmac.compare_digest(signature, expected):
            return None
        payload = json.loads(raw)
        if payload.get("v") != 2 or not isinstance(payload.get("magic_id"), int) or not isinstance(payload.get("telegram_id"), int):
            return None
        if datetime.now(timezone.utc).timestamp() - int(payload.get("ts", 0)) > SESSION_TTL_SECONDS:
            return None
        return payload
    except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError):
        return None


def _state_dir() -> str:
    return require_state_dir()


def _super_admins() -> set[int]:
    """Read the WebUI-admin allowlist as a set of contact ids.

    The identity is the **contact (uid), not the per-channel
    delivery address — a contact with a bound TG chat is the
    same identity as that same contact signing in via
    WebUI. The legacy ``telegram.super_admins`` meta key
    (pre-D.24) stores raw TG chat ids; the fallback path
    resolves each legacy chat id to its current ``Contact.id``
    so old state files keep working.

    Source of truth is the ``contacts`` table — the
    ``admin=True`` boolean. Replaces the pre-2024
    ``role='admin'`` filter (admin was split from role when
    the two concepts collided on the served user who is
    also an operator).
    """
    if control_store.enabled():
        from magi.db.magis import open_magis_session
        with open_magis_session() as session:
            return set(session.scalars(select(ControlOperator.id).where(ControlOperator.admin.is_(True))).all())
    state_dir = _state_dir()
    result: set[int] = set()
    try:
        with open_session() as session:
            for ct in session.scalars(
                select(Contact).where(Contact.admin == 1)
            ).all():
                result.add(contact.id)
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
    # Legacy entries were TG chat ids (decimal digit
    # strings). Resolve each to the current Contact.id via
    # the ``telegram_id`` column (the bot's inbound-handler
    # read-cache) so the cookie identity matches the new
    # schema.
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
                select(Contact).where(
                    Contact.telegram_id.in_(legacy_chat_ids)
                )
            ).all()
            return {contact.id for ct in rows}
    except Exception:
        logger.exception("super_admins: legacy meta lookup failed")
        return set()


# -- request / response schemas -----------------------------------------


class AllowedLoginAccount(BaseModel):
    """One row in the login-page dropdown.

    ``uid`` is the cross-channel identity. ``name`` is the
    contact's display name (shown in the dropdown).
    """

    uid: int
    name: str
    display_name: str | None = None


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

    D.24: the cookie is keyed by ``uid`` (the cross-
    channel identity), not the per-channel delivery
    address. The response surfaces the contact identity
    so the frontend can display it directly; the
    ``telegram_id`` is the bound TG chat id (may be
    ``None`` for admins who never bound a TG bot) and
    is exposed for any future "send to my TG"
    affordance.
    """

    uid: int
    telegram_id: int | None = None
    display_name: str | None = None
    admin: bool = True  # sourced from the contact row; pre-2024 was a hardcoded True
    assigned: bool = False
    selected_magic_id: int | None = None

    # D.24: the cookie is keyed by ``uid``, not
    # the per-channel delivery address. The response
    # surfaces the contact identity so the frontend
    # can display it directly; the ``telegram_id`` is
    # the bound TG chat id (may be ``None`` for admins
    # who never bound a TG bot) and is exposed for any
    # future "send to my TG" affordance.


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
# TG client API uses a vendor-fixed kwarg name on the
# contract, but that's an internal detail of the
# TG channel adapter — callers never see a chat id.

_LOGIN_KEY = "auth.login_code"


def _load_login_code(uid: int) -> dict | None:
    from magi.db.settings import state_get

    raw = control_store.get(f"{_LOGIN_KEY}.{uid}") if control_store.enabled() else state_get(_state_dir(), f"{_LOGIN_KEY}.{uid}")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


def _store_login_code(uid: int, code: str, issued_at: datetime, expires_at: float) -> None:
    from magi.db.settings import state_set

    value = json.dumps(
            {
                "code": code,
                "issued_at": issued_at.replace(microsecond=0).isoformat(),
                "expires_at": expires_at,
                "last_sent_at": issued_at.timestamp(),
            }
        )
    if control_store.enabled():
        control_store.set(f"{_LOGIN_KEY}.{uid}", value)
    else:
        state_set(_state_dir(), f"{_LOGIN_KEY}.{uid}", value)


def _clear_login_code(uid: int) -> None:
    from magi.db.settings import state_delete

    if control_store.enabled():
        control_store.delete(f"{_LOGIN_KEY}.{uid}")
    else:
        state_delete(_state_dir(), f"{_LOGIN_KEY}.{uid}")


# -- endpoints ---------------------------------------------------------


class AvailableMAGI(BaseModel):
    id: int
    name: str | None = None


class AvailableMAGIResponse(BaseModel):
    magi: list[AvailableMAGI]


class TargetLoginRequest(BaseModel):
    telegram_id: int


class TargetVerifyRequest(TargetLoginRequest):
    code: str = Field(min_length=6, max_length=6)


async def _target_access(magic_id: int, method: str, path: str, payload: dict[str, object] | None = None) -> dict[str, object]:
    """Call a target runtime before a browser identity exists.

    This is still authenticated service-to-service: ``operator_id=0`` marks
    the narrow pre-login capability, and the signature remains bound to the
    selected runtime id and exact path.
    """
    from magi.db.magis import open_magis_session
    from magi.channels.webui.api.runtime_proxy import _runtime_url

    with open_magis_session() as session:
        base = _runtime_url(session, magic_id)
    headers = build_proxy_headers(
        method=method, path_and_query=path, target_id=magic_id,
        operator_id=0, operator_name="WebUI login", telegram_id=None,
    )
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.request(method, base + path, json=payload, headers=headers)
    except httpx.HTTPError as exc:
        raise MagiHTTPException(503, "access.runtime_unreachable", "Selected MAGI runtime is unreachable") from exc
    try:
        body = response.json()
    except ValueError:
        body = {"detail": "Selected MAGI returned an invalid response"}
    if response.is_error:
        raise MagiHTTPException(response.status_code, str(body.get("code", "access.target_error")), str(body.get("detail", "Target request failed")))
    return body


@router.get("/available-magi", response_model=AvailableMAGIResponse)
async def available_magi() -> AvailableMAGIResponse:
    """List login targets that are currently running.

    The control deployment reads runtime registry metadata only.  It does not
    read a target's local workspace or user records.
    """
    from magi.db.magis import open_magis_session
    with open_magis_session() as session:
        rows = session.scalars(select(MAGIC).order_by(MAGIC.id)).all()
        runtimes = {row.magic_id: row for row in session.scalars(select(EveRuntime)).all()}
        root = session.scalar(select(MAGIS).where(MAGIS.parent_id.is_(None)).order_by(MAGIS.id))
        result = [
            AvailableMAGI(id=row.id, name=row.name)
            for row in rows
            if (root is not None and root.adam_id == row.id)
            # The orchestrator records ``provisioning`` immediately after
            # creating the Deployment; Kubernetes does not synchronously
            # write a later observed state back to this registry.  Desired
            # running is therefore the durable availability signal here.
            or (runtimes.get(row.id) is not None and runtimes[row.id].desired_state == "running")
        ]
    return AvailableMAGIResponse(magi=result)


@router.get("/targets/{magic_id}/accounts")
async def target_accounts(magic_id: int) -> dict[str, object]:
    return await _target_access(magic_id, "GET", "/api/access/login-accounts")


@router.post("/targets/{magic_id}/send-login-code")
async def target_send_login_code(magic_id: int, payload: TargetLoginRequest) -> dict[str, object]:
    return await _target_access(magic_id, "POST", "/api/access/send-login-code", payload.model_dump())


@router.post("/targets/{magic_id}/verify-login-code", response_model=VerifyLoginCodeResponse)
async def target_verify_login_code(magic_id: int, payload: TargetVerifyRequest, response: Response) -> VerifyLoginCodeResponse:
    result = await _target_access(magic_id, "POST", "/api/access/verify-login-code", payload.model_dump())
    if not result.get("ok"):
        return VerifyLoginCodeResponse(ok=False, error=str(result.get("error") or "Code does not match"))
    telegram_id = result.get("telegram_id")
    if not isinstance(telegram_id, int):
        raise MagiHTTPException(502, "access.target_invalid_identity", "Selected MAGI returned an invalid identity")
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=_sign_selected_session(
            magic_id=magic_id, telegram_id=telegram_id,
            display_name=result.get("display_name") if isinstance(result.get("display_name"), str) else None,
            admin=bool(result.get("admin")), assigned=bool(result.get("assigned")),
        ),
        max_age=SESSION_TTL_SECONDS, httponly=True, samesite="lax", path="/",
    )
    return VerifyLoginCodeResponse(ok=True)


@router.get("/allowed-accounts", response_model=AllowedLoginAccountsResponse)
async def list_allowed_accounts() -> AllowedLoginAccountsResponse:
    """The list of UIDs that can log in to Adam.

    UID is the row's identity; the dropdown's primary key is
    the UID, not the IM chat id. We still include
    ``telegram_id`` in the response so the frontend can show
    a contact as e.g. "EVA-00 PROTO TYPE (Telegram: 9999001)"
    — useful when two contacts share a display name — but
    the wire-protocol ask for the verification code takes the
    UID, not a chat id.

    Filter: admin rows that have at least one bound IM
    (today: ``telegram_id IS NOT NULL``). An admin who never
    bound a TG chat can't receive the verification code
    so they have no login affordance; the row is dropped
    rather than greyed out so the dropdown stays small.

    Two sources, unioned:

    1. ``role='admin'`` rows in ``contacts`` — the
       wizard-configured deployer list. role =
       ``super_admin``.
    2. Contacts with a bound TG chat + an active EVE
       assignment. role = ``assigned_contact``. C2 wires
       the TG binding (the contact proves ownership of the
       chat from TG by replying to a code); C6 wires the
       EVE dispatch (Adam spawns a container for the
       contact). The two together mean "this person has a
       live EVE they manage" — they should be able to sign
       in to see its logs, change its skills, etc., without
       needing deployer-level access.

    For C0 the contacts side is empty (the tables don't
    exist yet — C1.1 lands the ORM). The path is wired so
    the frontend can show "0 assigned contacts" today and
    start populating as soon as C6 dispatches the first EVE.

    Display names come from the local ``Contact`` row only.
    We intentionally avoid Telegram ``getChat`` network calls here:
    login must stay fast even when Telegram is slow or blocked.
    """
    if control_store.enabled():
        from magi.db.magis import open_magis_session
        with open_magis_session() as session:
            operators = session.scalars(select(ControlOperator).where(ControlOperator.admin.is_(True))).all()
        return AllowedLoginAccountsResponse(accounts=[
            AllowedLoginAccount(uid=operator.id, name=operator.display_name or f"Admin {operator.telegram_id}")
            for operator in operators
        ])
    accounts: list[AllowedLoginAccount] = []

    # 1. Super admins (wizard-configured). We resolve
    # each uid → its bound TG chat id so the frontend
    # can show the chat hint next to the display name.
    # Admins without a TG binding are dropped (no IM to
    # deliver the verification code through).
    admin_uids = _super_admins()
    admin_rows: list[tuple[int, int, str | None]] = []  # (uid, telegram_id, name)
    if admin_uids:
        with open_session() as session:
            for ct in session.scalars(
                select(Contact).where(Contact.id.in_(admin_uids))
            ).all():
                if contact.telegram_id is not None:
                    admin_rows.append(
                        (contact.id, contact.telegram_id, contact.display_name or contact.name)
                    )

    candidates: dict[int, tuple[int, str | None]] = {
        uid: (telegram_id_int, fallback_name)
        for uid, telegram_id_int, fallback_name in admin_rows
    }

    for uid, (_telegram_id_int, fallback_name) in sorted(candidates.items()):
        accounts.append(
            AllowedLoginAccount(
                uid=uid,
                name=fallback_name or f"uid {uid}",
            )
        )

    # 2. Assigned contacts. C0: the contacts / eves tables don't
    # exist yet, so this list is always empty. The query is
    # sketched in the comment so C1.1 / C6 can drop it in.
    #
    #   SELECT e.uid, e.telegram_id, e.name
    #   FROM contacts e
    #   JOIN eves v ON v.uid = e.id
    #   WHERE e.telegram_id IS NOT NULL
    #     AND v.status != 'shutting_down'
    #     AND NOT EXISTS (
    #       SELECT 1 FROM contacts a
    #       WHERE a.uid = e.uid AND a.role = 'admin'
    #     );
    #
    # We skip the join and rely on the frontend to de-dupe
    # super_admins from the visible list (super admins should
    # not also appear under "assigned contacts" — they manage
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

    if control_store.enabled():
        from magi.db.magis import open_magis_session
        with open_magis_session() as session:
            operator = session.get(ControlOperator, uid)
        if operator is None or not operator.admin:
            return SendLoginCodeResponse(ok=True, expires_in=_CODE_TTL_SECONDS)
        previous = _load_login_code(uid)
        if previous and datetime.now(timezone.utc).timestamp() - float(previous.get("last_sent_at", 0)) < _RESEND_COOLDOWN_SECONDS:
            return SendLoginCodeResponse(ok=False, error="Wait before requesting a new code.")
        code = _generate_code()
        issued_at = datetime.now(timezone.utc)
        _store_login_code(uid, code, issued_at, issued_at.timestamp() + _CODE_TTL_SECONDS)
        from magi.channels.webui.control_runtime import send_telegram
        try:
            await send_telegram(operator.telegram_id, f"Your MAGI sign-in code is: <code>{code}</code>")
        except Exception as exc:
            _clear_login_code(uid)
            return SendLoginCodeResponse(ok=False, error=f"send failed: {exc}")
        return SendLoginCodeResponse(ok=True, expires_in=_CODE_TTL_SECONDS)
    # Resolve the UID's bound IM channel via the channel
    # dispatcher (D.28). Today: TG. Future: dispatcher picks
    # the first channel with a live bot. If no IM is
    # bound, refuse — we have no way to deliver the code.
    from magi.channels import dispatcher as channel_dispatcher
    im_id = channel_dispatcher.lookup_im_id(uid, Channel.TG)
    if im_id is None:
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
        await channel_dispatcher.send_to_uid(uid, Channel.TG, code_text)
    except RuntimeError as e:
        # Lazy-start path: token exists but no live bot
        # instance yet. Try booting the TG bot once, then
        # retry dispatcher send briefly before falling back
        # to raw HTTP send.
        try:
            tg_bot.start_bot(_state_dir())
            for _ in range(4):
                await asyncio.sleep(0.25)
                if tg_bot.get_telegram_bot() is not None:
                    break
            await channel_dispatcher.send_to_uid(uid, Channel.TG, code_text)
            return SendLoginCodeResponse(ok=True, expires_in=_CODE_TTL_SECONDS)
        except Exception:
            # Fall through to raw-send fallback below.
            pass

        # Raw-send fallback keeps first login usable even if
        # the polling bot is still starting or unavailable.
        try:
            await tg_bot.send_text_auto(int(im_id), code_text)
        except Exception as raw_exc:
            _clear_login_code(uid)
            return SendLoginCodeResponse(
                ok=False,
                error=f"send failed: {raw_exc}",
            )
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
    # ``Contact`` row to gate access. C8 will replace
    # this with a signed token + a real session table.
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=_sign_uid(uid),
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
    row in ``contacts`` with role='admin'. D.24 — the
    cookie is the uid (cross-channel identity),
    not the chat id the user typed on the login page.
    The ``telegram_id`` field in the response is the
    bound TG chat id, looked up from the same row.
    """
    from magi.channels.webui.api.errors import MagiHTTPException

    selected = selected_session(magi_session)
    if selected is not None:
        return MeResponse(
            uid=int(selected["telegram_id"]), telegram_id=int(selected["telegram_id"]),
            display_name=selected.get("display_name") if isinstance(selected.get("display_name"), str) else None,
            admin=bool(selected.get("admin")), assigned=bool(selected.get("assigned")),
            selected_magic_id=int(selected["magic_id"]),
        )
    uid = _verify_signed_uid(magi_session or "")
    if uid is None or uid not in _super_admins():
        raise MagiHTTPException(
            status_code=401, code="auth.not_signed_in", detail="Not signed in"
        )
    if control_store.enabled():
        from magi.db.magis import open_magis_session
        with open_magis_session() as session:
            operator = session.get(ControlOperator, uid)
        if operator is None:
            raise MagiHTTPException(status_code=401, code="auth.not_signed_in", detail="Not signed in")
        return MeResponse(uid=operator.id, telegram_id=operator.telegram_id, display_name=operator.display_name, admin=operator.admin)
    # We already proved the cookie is a valid admin
    # uid; re-read the row to surface the
    # telegram_id + display name. If the row has since
    # been deleted (admin removed mid-session), we fall
    # back to None — the cookie is still good for this
    # request, just no metadata to enrich the response
    # with.
    try:
        with open_session() as session:
            contact = session.get(Contact, uid)
        if contact is None:
            return MeResponse(
                uid=uid,
                telegram_id=None,
                display_name=None,
                admin=False,
            )
        return MeResponse(
            uid=contact.id,
            telegram_id=contact.telegram_id,
            display_name=contact.name,
            admin=bool(contact.admin),
        )
    except Exception:
        logger.exception("me: contact lookup failed for cookie value")
        return MeResponse(uid=uid, admin=False)
