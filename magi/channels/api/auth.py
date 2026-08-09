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
  who bound one) is read from ``Contact.telegram_id``.
  A contact can be bound to multiple channels — the cookie identity stays stable
  across all of them. ``/me`` resolves the cookie's
  contact id to the row and reports both ``uid`` and
  ``telegram_id`` (the latter may be ``None`` for admins
  who never bound a TG bot).

  Reads (list / get sessions) are scoped by ``uid``
  on the server side (see the bus session service) —
  a contact sees their own history across every channel,
  regardless of which one was used to create a given row.
  Writes (continue-send / append) are still channel-owned
  via D.22's :class:`ChannelMismatchError`: a contact can
  only continue a conversation on the channel that
  originally created it.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from base64 import urlsafe_b64decode, urlsafe_b64encode
from datetime import datetime, timezone
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Cookie, Request, Response
from pydantic import BaseModel, Field

from magi.bus import Bus
from magi.bus.guild.deliveryJob import DeliveryJob
from magi.channels.api import control_store
from magi.channels.api.auth_gates import AdminGate
from magi.channels.api.dependencies import BusDep, get_bus
from magi.channels.api.errors import MagiHTTPException
from magi.channels.api.proxy_auth import build_proxy_headers

logger = logging.getLogger("magi.api.auth")

router = APIRouter(tags=["auth"])


# Same TTL / cooldown as the admin code — reuses the user's mental
# model. Could be tuned later; for now identical is fine.
_CODE_TTL_SECONDS = 300
_RESEND_COOLDOWN_SECONDS = 60

SESSION_COOKIE_NAME = "magi_session"
SESSION_TTL_SECONDS = 14 * 24 * 60 * 60


def _signing_key(bus: Bus) -> bytes:
    """Derive a signing key from the bootstrap-generated secret.

    The secret is stored in ``settings_book`` at key
    ``auth.signing_key`` and is generated once at first boot.
    Falls back to ``os.urandom`` if settings_book hasn't been
    seeded yet (e.g. very early in the control-plane path).
    """
    if control_store.enabled():
        secret = os.environ.get("MAGI_CONTROL_SECRET")
        if secret:
            return hashlib.sha256(secret.encode() + b"magi-control-session").digest()
    raw = bus.settings_book.get(key="auth.signing_key")
    if raw:
        return hashlib.sha256(raw.encode() + b"magi-session-signing").digest()
    # Safety net: generate a one-off key if settings_book isn't
    # seeded yet.  The bootstrap path always sets this, but the
    # control-plane path short-circuits before bus init.
    import secrets
    return secrets.token_bytes(32)


def _sign_uid(bus: Bus, uid: int) -> str:
    """Return ``uid:timestamp:hmac`` signed token."""
    ts = str(int(datetime.now(timezone.utc).timestamp()))
    payload = f"{uid}:{ts}".encode()
    sig = hmac.new(_signing_key(bus), payload, hashlib.sha256).hexdigest()[:16]
    token = f"{uid}:{ts}:{sig}".encode()
    return urlsafe_b64encode(token).decode().rstrip("=")


def _verify_signed_uid(bus: Bus, token: str) -> int | None:
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
        expected = hmac.new(_signing_key(bus), payload, hashlib.sha256).hexdigest()[:16]
        if not hmac.compare_digest(sig, expected):
            return None
        return uid
    except Exception:
        return None


def _sign_selected_session(bus: Bus, *, magic_id: int, telegram_id: int, display_name: str | None, admin: bool, assigned: bool) -> str:
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
    signature = hmac.new(_signing_key(bus), raw, hashlib.sha256).hexdigest()[:24]
    return "v2." + urlsafe_b64encode(raw).decode().rstrip("=") + "." + signature


def selected_session(bus: Bus, token: str | None) -> dict[str, Any] | None:
    """Return the selected-MAGI browser session, if valid and unexpired."""
    if not token or not token.startswith("v2."):
        return None
    try:
        _version, body, signature = token.split(".", 2)
        raw = urlsafe_b64decode(body + "=" * (-len(body) % 4))
        expected = hmac.new(_signing_key(bus), raw, hashlib.sha256).hexdigest()[:24]
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


def _super_admins(bus: Bus) -> set[int]:
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
        admins = bus.magis_admins_book
        return {admin.uid for admin in admins.list_for_magis(magis_id=1)} if admins else set()
    contacts = bus.contacts_book.list_admins()
    return {contact.id for contact in contacts}


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
    # ``login_methods`` + ``password_set`` mirror the
    # :class:`AuthCredential` table + the bound IM. The
    # Settings → Security card renders the form from
    # these; the LoginPage uses ``/api/auth/login-methods``
    # so it doesn't need a valid session.
    login_methods: list[str] = []
    password_set: bool = False

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


def _load_login_code(bus: Bus, uid: int) -> dict | None:
    raw = (
        control_store.get(bus, f"{_LOGIN_KEY}.{uid}")
        if control_store.enabled()
        else bus.settings_book.get(key=f"{_LOGIN_KEY}.{uid}")
    )
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


def _store_login_code(bus: Bus, uid: int, code: str, issued_at: datetime, expires_at: float) -> None:
    value = json.dumps(
        {
            "code": code,
            "issued_at": issued_at.replace(microsecond=0).isoformat(),
            "expires_at": expires_at,
            "last_sent_at": issued_at.timestamp(),
        }
    )
    if control_store.enabled():
        control_store.set(bus, f"{_LOGIN_KEY}.{uid}", value)
    else:
        bus.settings_book.set(key=f"{_LOGIN_KEY}.{uid}", value=value)


def _clear_login_code(bus: Bus, uid: int) -> None:
    if control_store.enabled():
        control_store.delete(bus, f"{_LOGIN_KEY}.{uid}")
    else:
        bus.settings_book.delete(key=f"{_LOGIN_KEY}.{uid}")


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


async def _target_access(bus: Bus, magic_id: int, method: str, path: str, payload: dict[str, object] | None = None) -> dict[str, Any]:
    """Call a target runtime before a browser identity exists.

    This is still authenticated service-to-service: ``operator_id=0`` marks
    the narrow pre-login capability, and the signature remains bound to the
    selected runtime id and exact path.
    """
    try:
        runtime = bus.eva_runtimes_book.get(runtime_id=magic_id) if bus.eva_runtimes_book else None
        base = getattr(runtime, "base_url", None) if runtime else None
        if not base:
            raise RuntimeError("runtime unavailable")
    except RuntimeError as exc:
        raise MagiHTTPException(503, "access.runtime_unreachable", "Selected MAGI runtime is unreachable") from exc
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
    # This endpoint is a control-plane capability.  Until the control
    # registry exposes its DTO query through Bus, a runtime-local app has no
    # targets to disclose.
    result: list[AvailableMAGI] = []
    return AvailableMAGIResponse(magi=result)


@router.get("/targets/{magic_id}/accounts")
async def target_accounts(magic_id: int, bus: BusDep) -> dict[str, object]:
    return await _target_access(bus, magic_id, "GET", "/api/access/login-accounts")


@router.post("/targets/{magic_id}/send-login-code")
async def target_send_login_code(magic_id: int, payload: TargetLoginRequest, bus: BusDep) -> dict[str, object]:
    return await _target_access(bus, magic_id, "POST", "/api/access/send-login-code", payload.model_dump())


@router.post("/targets/{magic_id}/verify-login-code", response_model=VerifyLoginCodeResponse)
async def target_verify_login_code(magic_id: int, payload: TargetVerifyRequest, response: Response, bus: BusDep) -> VerifyLoginCodeResponse:
    result = await _target_access(bus, magic_id, "POST", "/api/access/verify-login-code", payload.model_dump())
    if not result.get("ok"):
        return VerifyLoginCodeResponse(ok=False, error=str(result.get("error") or "Code does not match"))
    telegram_id = result.get("telegram_id")
    if not isinstance(telegram_id, int):
        raise MagiHTTPException(502, "access.target_invalid_identity", "Selected MAGI returned an invalid identity")
    raw_display_name = result.get("display_name")
    display_name = raw_display_name if isinstance(raw_display_name, str) else None
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=_sign_selected_session(bus,
            magic_id=magic_id, telegram_id=telegram_id,
            display_name=display_name,
            admin=bool(result.get("admin")), assigned=bool(result.get("assigned")),
        ),
        max_age=SESSION_TTL_SECONDS, httponly=True, samesite="lax", path="/",
    )
    return VerifyLoginCodeResponse(ok=True)


@router.get("/allowed-accounts", response_model=AllowedLoginAccountsResponse)
async def list_allowed_accounts(bus: BusDep) -> AllowedLoginAccountsResponse:
    """The list of UIDs that can log in to ADAM.

    UID is the row's identity; the dropdown's primary key is
    the UID, not the IM chat id. We still include
    ``telegram_id`` in the response so the frontend can show
    a contact as e.g. "EVA-000 (Telegram: 9999001)"
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
    2. Contacts with a bound TG chat + an active EVA
       assignment. role = ``assigned_contact``. C2 wires
       the TG binding (the contact proves ownership of the
       chat from TG by replying to a code); C6 wires the
       EVA dispatch (ADAM spawns a container for the
       contact). The two together mean "this person has a
       live EVA they manage" — they should be able to sign
       in to see its logs, change its skills, etc., without
       needing deployer-level access.

    For C0 the contacts side is empty (the tables don't
    exist yet — C1.1 lands the ORM). The path is wired so
    the frontend can show "0 assigned contacts" today and
    start populating as soon as C6 dispatches the first EVA.

    Display names come from the local ``Contact`` row only.
    We intentionally avoid Telegram ``getChat`` network calls here:
    login must stay fast even when Telegram is slow or blocked.
    """
    if control_store.enabled():
        return AllowedLoginAccountsResponse(accounts=[])
    accounts: list[AllowedLoginAccount] = []

    # 1. Super admins (wizard-configured). We resolve
    # each uid → its bound TG chat id so the frontend
    # can show the chat hint next to the display name.
    # Admins without a TG binding are dropped (no IM to
    # deliver the verification code through).
    admin_uids = _super_admins(bus)
    candidates: dict[int, tuple[int | None, str | None]] = {}
    if admin_uids:
        for uid in admin_uids:
            contact = bus.contacts_book.get(contact_id=uid)
            if contact is None or contact.telegram_id is None:
                continue
            candidates[contact.id] = (
                contact.telegram_id,
                contact.display_name or contact.name,
            )

    for uid, (_telegram_id_int, fallback_name) in sorted(candidates.items()):
        accounts.append(
            AllowedLoginAccount(
                uid=uid,
                name=fallback_name or f"uid {uid}",
            )
        )

    # 2. Assigned contacts. C0: the contacts / evas tables don't
    # exist yet, so this list is always empty. The query is
    # sketched in the comment so C1.1 / C6 can drop it in.
    #
    #   SELECT e.uid, e.telegram_id, e.name
    #   FROM contacts e
    #   JOIN evas v ON v.uid = e.id
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
    # the system, not a single EVA).

    return AllowedLoginAccountsResponse(accounts=accounts)


@router.post("/send-login-code", response_model=SendLoginCodeResponse)
async def send_login_code(
    payload: SendLoginCodeRequest,
    bus: BusDep,
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

    if uid not in _super_admins(bus):
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
        return SendLoginCodeResponse(ok=False, error="control-plane login is unavailable")
    # Contact is the single source of truth for the current Telegram binding.
    # Resolve it before recording a login code, so an unbound operator cannot
    # accumulate credentials that were never delivered.
    contact = bus.contacts_book.get(contact_id=uid)
    if contact is None or contact.telegram_id is None:
        return SendLoginCodeResponse(
            ok=False,
            error="This account has no IM channel configured; "
                  "ask the deployer to bind a TG chat first.",
        )

    # Cooldown — same anti-abuse logic as the admin code.
    # Storage is keyed by UID so a future second-IM
    # binding doesn't leave half-stored codes behind.
    previous = _load_login_code(bus, uid)
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
    _store_login_code(bus, uid, code, issued_at, expires_at)

    # API code creates a durable delivery intent; the Telegram worker owns
    # bot I/O, retries, and final delivery status.
    code_text = (
        f"Your MAGI sign-in code is: <code>{code}</code>\n\n"
        f"Enter it in the browser to log in. The code "
        f"expires in {_CODE_TTL_SECONDS // 60} minutes."
    )
    try:
        bus.delivery_job_board.publish(
            DeliveryJob(
                channel="tg",
                destination=str(contact.telegram_id),
                payload={"text": code_text, "uid": uid},
            )
        )
    except Exception as exc:
        _clear_login_code(bus, uid)
        return SendLoginCodeResponse(ok=False, error=f"send failed: {exc}")

    return SendLoginCodeResponse(ok=True, expires_in=_CODE_TTL_SECONDS)


@router.post("/verify-login-code", response_model=VerifyLoginCodeResponse)
async def verify_login_code(
    payload: VerifyLoginCodeRequest,
    response: Response,
    bus: BusDep,
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

    if uid not in _super_admins(bus):
        # Anti-enumeration: same response as a wrong code.
        return VerifyLoginCodeResponse(ok=False, error="Code does not match")

    stored = _load_login_code(bus, uid)
    if not stored:
        return VerifyLoginCodeResponse(
            ok=False,
            error="No code sent to this uid — request a new one.",
        )

    try:
        expires_at = float(stored.get("expires_at", 0))
    except (TypeError, ValueError):
        expires_at = 0
    if not expires_at or datetime.now(timezone.utc).timestamp() >= expires_at:
        _clear_login_code(bus, uid)
        return VerifyLoginCodeResponse(
            ok=False, error="Code expired — request a new one."
        )

    # Burn on any path past expiry (mismatch, success, anything) so
    # the code can't be retried.
    _clear_login_code(bus, uid)

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
        value=_sign_uid(bus, uid),
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
    bus: BusDep,
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
    selected = selected_session(bus, magi_session)
    if selected is not None:
        telegram_id_value = int(selected["telegram_id"])
        magic_id_value = int(selected["magic_id"])
        raw_display_name = selected.get("display_name")
        display_name = raw_display_name if isinstance(raw_display_name, str) else None
        return MeResponse(
            uid=telegram_id_value, telegram_id=telegram_id_value,
            display_name=display_name,
            admin=bool(selected.get("admin")), assigned=bool(selected.get("assigned")),
            selected_magic_id=magic_id_value,
        )
    uid = _verify_signed_uid(bus, magi_session or "")
    if uid is None or uid not in _super_admins(bus):
        raise MagiHTTPException(
            status_code=401, code="auth.not_signed_in", detail="Not signed in"
        )
    if control_store.enabled():
        raise MagiHTTPException(status_code=401, code="auth.not_signed_in", detail="Not signed in")
    # We already proved the cookie is a valid admin
    # uid; re-read the row to surface the
    # telegram_id + display name. If the row has since
    # been deleted (admin removed mid-session), we fall
    # back to None — the cookie is still good for this
    # request, just no metadata to enrich the response
    # with.
    try:
        contact = bus.contacts_book.get(contact_id=uid)
        if contact is None:
            return MeResponse(
                uid=uid,
                telegram_id=None,
                display_name=None,
                admin=False,
            )
        methods, _is_webui_only = _login_methods_for(bus, uid)
        return MeResponse(
            uid=contact.id,
            telegram_id=contact.telegram_id,
            display_name=contact.name,
            admin=bool(contact.admin),
            login_methods=methods,
            password_set="password" in methods,
        )
    except Exception:
        # Defensive: a transient DB hiccup shouldn't
        # lock the operator out of ``/me``. We saw the
        # cookie is valid (uid in _super_admins); the
        # metadata lookup is best-effort.
        logger.exception("me: contact lookup failed for uid=%s", uid)
        return MeResponse(
            uid=uid,
            telegram_id=None,
            display_name=None,
            admin=False,
        )


# -- Password login + credential management -------------------------------
#
# Four endpoints plus a helper used by the LoginPage to
# pick which tab to render. The TG code flow above is
# unchanged; password login is a parallel route that
# co-exists with the code route so an operator can switch
# back and forth. The 60s cooldown is shared per-uid
# (success or failure both reset the timer) so a typo
# followed by a successful retry still has to wait a
# minute — same UX as the TG code path.


class LoginMethodsResponse(BaseModel):
    """Which login methods are available for ``uid``.

    The frontend uses this to decide whether to show a
    "password" tab, a "TG code" tab, or both. Steered by
    the :class:`AuthCredential` table on the single-MAGI
    path and the ``magis_admins`` row (bound Telegram
    chat id) on the control-plane path.
    """

    uid: int
    methods: list[str] = []
    # True when the user has a Contact row with no
    # telegram_id — i.e. they're a WebUI-only operator.
    is_webui_only: bool = False


class LoginPasswordRequest(BaseModel):
    uid: int
    password: str


class LoginPasswordResponse(BaseModel):
    ok: bool
    error: str | None = None
    # ``retry_after`` is set when the cooldown is active
    # so the frontend can disable the button for the
    # remaining seconds instead of letting the user spam
    # retries.
    retry_after: int | None = None


class SetPasswordRequest(BaseModel):
    uid: int
    password: str
    # ``old_password`` is required for self-service change
    # and ignored when the caller is admin and changing
    # someone else's password (the admin path is gated by
    # ``AdminGate``).
    old_password: str | None = None


class SetPasswordResponse(BaseModel):
    ok: bool
    error: str | None = None


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str


def _password_hash(bus: Bus, uid: int) -> str | None:
    """Return the local password credential without exposing persistence."""
    if bus.auth_credentials_book is not None:
        credential = bus.auth_credentials_book.find(uid=uid, kind="password")
        return credential.secret_hash if credential else None
    raw = bus.settings_book.get(key=f"auth.password.{uid}.hash")
    return raw or None


def _set_password_hash(bus: Bus, uid: int, secret_hash: str) -> None:
    if bus.auth_credentials_book is not None:
        existing = bus.auth_credentials_book.find(uid=uid, kind="password")
        if existing is not None:
            bus.auth_credentials_book.delete(credential_id=existing.id)
        bus.auth_credentials_book.add(uid=uid, kind="password", secret_hash=secret_hash)
        return
    bus.settings_book.set(key=f"auth.password.{uid}.hash", value=secret_hash)


def _delete_password_hash(bus: Bus, uid: int) -> bool:
    if bus.auth_credentials_book is not None:
        existing = bus.auth_credentials_book.find(uid=uid, kind="password")
        return bool(existing and bus.auth_credentials_book.delete(credential_id=existing.id))
    return bus.settings_book.delete(key=f"auth.password.{uid}.hash")


def _login_methods_for(bus: Bus, uid: int) -> tuple[list[str], bool]:
    """Return ``(methods, is_webui_only)`` for ``uid``.

    Used by both the new ``GET /auth/login-methods`` route
    and the onboarding wizard's "is setup complete?"
    check. The two paths (single-MAGI vs control-plane)
    disagree on the data source — control-plane looks the
    admin up in ``magis_admins`` by the Telegram chat id
    (the value the WebUI uses as the login ``uid`` on
    that deployment mode); the single-MAGI path uses
    Contact + auth_credentials.
    """
    methods: list[str] = []
    is_webui_only = True

    if control_store.enabled():
        # Control-plane mode: ``uid`` is the operator's
        # Telegram chat id (the value the WebUI login
        # page hands in). The MAGIS admin roster
        # (``magis_admins``) is keyed by ``Contact.id``
        # (FK), so the lookup chain is
        # ``telegram_id → contacts.telegram_id → contact.id
        #   → magis_admins.uid``. A bound admin always
        # resolves and gets ``tg_code``; a missing row
        # means "this Telegram account isn't a MAGIS
        # admin here" and the login page falls back to
        # the empty-state affordance.
        contact = bus.contacts_book.get_by_telegram(telegram_id=uid)
        if contact is not None and bus.magis_admins_book is not None:
            if bus.magis_admins_book.is_admin_for(uid=contact.id):
                return (["tg_code"], False)
        return ([], True)

    contact = bus.contacts_book.get(contact_id=uid)
    if contact is None:
        return ([], True)
    if _password_hash(bus, uid):
        methods.append("password")
    if contact.telegram_id is not None:
        methods.append("tg_code")
        is_webui_only = False
    return (methods, is_webui_only)


@router.get("/login-methods", response_model=LoginMethodsResponse)
async def login_methods(uid: int, bus: BusDep) -> LoginMethodsResponse:
    """Public probe — returns the available login methods for ``uid``.

    Used by the LoginPage to decide which tabs to render.
    Anti-enumeration: returns ``methods=[]`` rather than
    404 when the uid doesn't exist (the LoginPage then
    shows "no login methods" and the operator can ping
    the admin). A future tightening can flip this to a
    404 once the WebUI treats uid as public knowledge.
    """
    methods, webui_only = _login_methods_for(bus, uid)
    return LoginMethodsResponse(
        uid=uid,
        methods=methods,
        is_webui_only=webui_only,
    )


@router.post("/login-password", response_model=LoginPasswordResponse)
async def login_password(
    payload: LoginPasswordRequest,
    response: Response,
    bus: BusDep,
) -> LoginPasswordResponse:
    """Verify password and set the session cookie.

    The 60s cooldown is enforced per-uid, regardless of
    the verify outcome. A successful verify calls
    :func:`password_utils.clear_attempt` so the user
    isn't locked out of their next login by the current
    one. The cooldown store is the same settings KV the
    TG-code path uses, just a different sub-prefix.

    Anti-enumeration: a uid that is not an admin
    responds as "ok" but never sets a cookie. Same shape
    as the ``send_login_code`` short-circuit.
    """
    if not payload.uid or not isinstance(payload.uid, int):
        return LoginPasswordResponse(ok=False, error="invalid uid")
    if not payload.password:
        return LoginPasswordResponse(ok=False, error="password required")

    if control_store.enabled():
        # Control-plane side doesn't have a password
        # store yet — out of scope for this PR.
        return LoginPasswordResponse(
            ok=False,
            error="password login is not supported by the control plane",
        )

    if payload.uid not in _super_admins(bus):
        # Anti-enumeration: respond as if the password
        # were wrong, but don't burn the cooldown.
        return LoginPasswordResponse(
            ok=False, error="password does not match",
        )

    from magi.channels.api import password_utils

    if not password_utils.check_cooldown(
        bus, payload.uid, cooldown_seconds=_RESEND_COOLDOWN_SECONDS,
    ):
        record = password_utils._store_get(bus, payload.uid) or {}
        last = float(record.get("last_attempt_at", 0))
        remaining = max(1, int(_RESEND_COOLDOWN_SECONDS - (_now_ts() - last)))
        return LoginPasswordResponse(
            ok=False,
            error=f"Wait {remaining}s before trying again.",
            retry_after=remaining,
        )

    # Always record the attempt — even on success — so
    # the post-login window is honest. The verify helper
    # clears the row on success below.
    password_utils.record_attempt(bus, payload.uid)

    stored = _password_hash(bus, payload.uid)
    if not stored or not password_utils.verify_password(stored, payload.password):
        return LoginPasswordResponse(
            ok=False,
            error="password does not match",
            retry_after=_RESEND_COOLDOWN_SECONDS,
        )

    password_utils.clear_attempt(bus, payload.uid)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=_sign_uid(bus, payload.uid),
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        path="/",
    )
    logger.info("user signed in via password", extra={"uid": payload.uid})
    return LoginPasswordResponse(ok=True)


def _now_ts() -> float:
    return datetime.now(timezone.utc).timestamp()


@router.post("/set-password", response_model=SetPasswordResponse)
async def set_password(
    payload: SetPasswordRequest,
    request: Request,
    _admin: AdminGate,
) -> SetPasswordResponse:
    """Set or replace a user's password.

    Both flows go through one endpoint:

      - **Self-service**: ``uid`` is the calling user's
        uid, ``old_password`` is required. The ``AdminGate``
        ensures the caller is signed in; the body check
        proves they own the row.
      - **Admin override**: an admin can set a password
        for any ``uid`` without ``old_password``. Used
        by the onboarding wizard's "set the first admin's
        password" path and the Settings → Security card
        "reset another admin's password".

    Control-plane side is out of scope (no
    password store yet).
    """
    if control_store.enabled():
        return SetPasswordResponse(
            ok=False,
            error="password login is not supported by the control plane",
        )

    bus = get_bus(request)
    from magi.channels.api import password_utils

    if not payload.uid or not isinstance(payload.uid, int):
        return SetPasswordResponse(ok=False, error="invalid uid")

    caller_uid = _verify_signed_uid(bus,
        request.cookies.get(SESSION_COOKIE_NAME) or "",
    )
    if caller_uid is None:
        return SetPasswordResponse(ok=False, error="not signed in")

    if caller_uid != payload.uid:
        # Admin override — AdminGate already proved the
        # caller is admin, so we skip the old_password
        # check.
        caller_contact = bus.contacts_book.get(contact_id=caller_uid)
        if caller_contact is None or not caller_contact.admin:
            return SetPasswordResponse(
                ok=False,
                error="only admins can set another user's password",
            )
    else:
        # Self-service: must know the existing password,
        # unless they're setting one for the first time.
        existing = _password_hash(bus, payload.uid)
        if existing is not None:
            if not payload.old_password:
                return SetPasswordResponse(
                    ok=False,
                    error="old_password required",
                )
            if not password_utils.verify_password(existing, payload.old_password):
                return SetPasswordResponse(
                    ok=False,
                    error="old_password does not match",
                )

    try:
        new_hash = password_utils.hash_password(payload.password)
    except ValueError as exc:
        return SetPasswordResponse(ok=False, error=str(exc))

    _set_password_hash(bus, payload.uid, new_hash)
    logger.info("password set", extra={"uid": payload.uid, "by": caller_uid})
    return SetPasswordResponse(ok=True)


@router.post("/change-password", response_model=SetPasswordResponse)
async def change_password(
    payload: ChangePasswordRequest,
    request: Request,
) -> SetPasswordResponse:
    """Self-service: change the signed-in user's password.

    Thin wrapper around :func:`set_password` that always
    uses the caller's uid as the target and requires
    ``old_password`` unconditionally. The frontend hits
    this from the Settings → Security card; the unified
    :func:`set_password` endpoint serves the admin
    override flow.
    """
    bus = get_bus(request)
    from magi.channels.api import password_utils

    caller_uid = _verify_signed_uid(bus,
        request.cookies.get(SESSION_COOKIE_NAME) or "",
    )
    if caller_uid is None:
        return SetPasswordResponse(ok=False, error="not signed in")
    try:
        new_hash = password_utils.hash_password(payload.new_password)
    except ValueError as exc:
        return SetPasswordResponse(ok=False, error=str(exc))

    existing = _password_hash(bus, caller_uid)
    if existing is None:
        return SetPasswordResponse(
            ok=False,
            error="no password set yet — use set-password instead",
        )
    if not password_utils.verify_password(existing, payload.old_password):
        return SetPasswordResponse(ok=False, error="old_password does not match")

    _set_password_hash(bus, caller_uid, new_hash)
    logger.info("password changed", extra={"uid": caller_uid})
    return SetPasswordResponse(ok=True)


@router.delete("/credentials/password/{uid}", status_code=204)
async def revoke_password(
    uid: int,
    _admin: AdminGate,
    bus: BusDep,
) -> Response:
    """Revoke a user's password login.

    After this call the user can only sign in via the
    remaining methods (TG code today). Admin-only — an
    operator who revokes their own password without
    another active method locks themselves out of the
    WebUI; the frontend warns before firing.
    """
    if control_store.enabled():
        return Response(status_code=204)
    if not _delete_password_hash(bus, uid):
        raise MagiHTTPException(
            status_code=404,
            code="auth.no_password_credential",
            detail=f"uid {uid} has no password credential",
        )
    logger.info("password revoked", extra={"uid": uid})
    return Response(status_code=204)
