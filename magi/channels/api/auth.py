"""Login / session API.

Two-step flow (mirror of admin verification):
    1. ``POST /api/auth/send-login-code { contact_id }``
       Sends a 6-digit code to the operator's bound TG chat via
       the saved bot. Same 5-min TTL and 60-s cooldown as admin
       code, stored under the same key namespace as a precaution.

    2. ``POST /api/auth/verify-login-code { contact_id, code }``
       On match, sets the ``magi_session`` cookie to the
       **contact_id** (stringified int) and returns 200. The cookie
       is HTTPOnly + SameSite=Lax; for C0 we skip signed-cookie /
       token-store machinery (C8 hardening).

Authorization model (D.24):
  The cookie's value identifies the **contact**, not the
  channel. The login input (``contact_id``) is a per-person
  identity; the cookie output is also the contact_id. The
  per-channel delivery address (TG chat id for admins
  who bound one) is read from ``Contact.tgid``.
  A contact can be bound to multiple channels — the cookie identity stays stable
  across all of them. ``/me`` resolves the cookie's
  contact id to the row and reports both ``contact_id`` and
  ``tgid`` (the latter may be ``None`` for admins
  who never bound a TG bot).

  Reads (list / get conversations) are scoped by ``contact_id``
  on the server side (see the bus conversation service) —
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
from datetime import UTC, datetime
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Cookie, Request, Response
from pydantic import BaseModel, Field

from magi.bus import Bus
from magi.bus.guild.deliveryJob import DeliveryJob
from magi.bus.library.magis.runtimeBook import RuntimeObservedState
from magi.channels.api import control_store
from magi.channels.api.auth_gates import AdminGate
from magi.channels.api.dependencies import BusDep, get_bus
from magi.channels.api.errors import MagiHTTPException
from magi.channels.api.proxy_auth import build_proxy_headers
from magi.channels.api.runtime_http import CONTROL_TIMEOUT

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


def _sign_contact_id(bus: Bus, contact_id: int) -> str:
    """Return ``contact_id:timestamp:hmac`` signed token."""
    ts = str(int(datetime.now(UTC).timestamp()))
    payload = f"{contact_id}:{ts}".encode()
    sig = hmac.new(_signing_key(bus), payload, hashlib.sha256).hexdigest()[:16]
    token = f"{contact_id}:{ts}:{sig}".encode()
    return urlsafe_b64encode(token).decode().rstrip("=")


def _verify_signed_contact_id(bus: Bus, token: str) -> int | None:
    """Return ``contact_id`` if the token is valid and not expired, else ``None``."""
    try:
        # Add padding back for base64 decode
        padded = token + "=" * (4 - len(token) % 4)
        decoded = urlsafe_b64decode(padded).decode()
        parts = decoded.split(":")
        if len(parts) != 3:
            return None
        contact_id_str, ts_str, sig = parts
        contact_id = int(contact_id_str)
        ts = int(ts_str)
        # Check expiry
        if datetime.now(UTC).timestamp() - ts > SESSION_TTL_SECONDS:
            return None
        # Verify signature
        payload = f"{contact_id}:{ts}".encode()
        expected = hmac.new(_signing_key(bus), payload, hashlib.sha256).hexdigest()[:16]
        if not hmac.compare_digest(sig, expected):
            return None
        return contact_id
    except Exception:
        return None


def _as_optional_int(value: object) -> int | None:
    """Coerce a cross-service JSON field to ``int | None``.

    Cookie payload fields are type-checked on the way back in
    (:func:`selected_session`), so a value that signs cleanly
    but fails that check produces a 401 that re-logging-in
    cannot clear — every attempt mints the same unusable
    cookie. Anything that isn't plainly an integer becomes
    ``None``, which is a legitimate value for ``tgid``.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.lstrip("-").isdigit():
        return int(value)
    return None


def _sign_selected_session(
    bus: Bus,
    *,
    magi_id: int,
    contact_id: int,
    tgid: int | None,
    display_name: str | None,
    admin: bool,
    assigned: bool,
) -> str:
    """Sign a browser session bound to exactly one selected MAGI.

    ``_sign_contact_id`` remains for compatibility with private-runtime tests and
    old single-node sessions. New control-plane sessions are versioned and
    include their selected target, so a browser cannot reuse a B session to
    proxy requests to C.

    Note: the payload key used to be ``"magic_id"`` (the legacy per-MAGI
    column name pre-rename). It is now ``"magi_id"``; the cookie version
    was bumped 2 → 3 to invalidate every existing signed v2 cookie at
    deploy time rather than risk a soft-mismatch.

    The same treatment applies to ``"telegram_id"`` → ``"tgid"``: the
    version was bumped 3 → 4 so every v3 cookie is rejected outright at
    deploy time. Operators are signed out once; the alternative is a
    payload whose key silently no longer matches what the reader expects.

    ``contact_id`` and ``tgid`` are separate payload fields on purpose.
    They are different identifiers (``contacts.id`` versus the Telegram
    chat id) and only the former is the session identity. Earlier v4
    drafts stored one slot that held a tgid when the contact had a TG
    binding and fell back to the contact_id when they did not, which
    made :func:`resolve_session` hand downstream callers a TG chat id
    in place of a contact_id for every TG-bound operator. ``tgid`` is
    nullable because a WebUI-only operator legitimately has none.
    """
    payload = {
        "v": 4,
        "magi_id": magi_id,
        "contact_id": contact_id,
        "tgid": tgid,
        "display_name": display_name,
        "admin": admin,
        "assigned": assigned,
        "ts": int(datetime.now(UTC).timestamp()),
    }
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
    signature = hmac.new(_signing_key(bus), raw, hashlib.sha256).hexdigest()[:24]
    return "v4." + urlsafe_b64encode(raw).decode().rstrip("=") + "." + signature


def selected_session(bus: Bus, token: str | None) -> dict[str, Any] | None:
    """Return the selected-MAGI browser session, if valid and unexpired."""
    if not token or not token.startswith("v4."):
        return None
    try:
        _version, body, signature = token.split(".", 2)
        raw = urlsafe_b64decode(body + "=" * (-len(body) % 4))
        expected = hmac.new(_signing_key(bus), raw, hashlib.sha256).hexdigest()[:24]
        if not hmac.compare_digest(signature, expected):
            return None
        payload = json.loads(raw)
        tgid = payload.get("tgid")
        if (
            payload.get("v") != 4
            or not isinstance(payload.get("magi_id"), int)
            or not isinstance(payload.get("contact_id"), int)
            or (tgid is not None and not isinstance(tgid, int))
        ):
            return None
        if datetime.now(UTC).timestamp() - int(payload.get("ts", 0)) > SESSION_TTL_SECONDS:
            return None
        return payload
    except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError):
        return None


def resolve_session(bus: Bus, token: str | None) -> dict[str, Any] | None:
    """Return a unified session payload for either cookie version.

    Reads from the request cookie (whatever version it is)
    and returns a single dict shape that downstream
    ``/me``, ``admin_gate``, ``set-password``,
    ``change-password``, ``chat.search`` and
    ``chat.conversations`` can consume without branching on
    v4 vs legacy. Returns ``None`` when the cookie is
    missing, malformed, expired, or points at a row that is
    no longer an admin.

    v4 cookies are the only kind issued by fresh login
    flows (verify-login-code, login-password, target verify);
    legacy cookies persist only until their TTL elapses.
    Both shapes are accepted here for a soft deprecation
    window.
    """
    selected = selected_session(bus, token)
    if selected is not None:
        try:
            return {
                "contact_id": int(selected["contact_id"]),
                "magi_id": int(selected["magi_id"]),
                "tgid": int(selected["tgid"]) if selected.get("tgid") is not None else None,
                "display_name": (
                    selected.get("display_name")
                    if isinstance(selected.get("display_name"), str)
                    else None
                ),
                "admin": bool(selected.get("admin")),
                "assigned": bool(selected.get("assigned")),
                "source": "v4",
            }
        except (KeyError, TypeError, ValueError):
            return None
    contact_id = _verify_signed_contact_id(bus, token or "")
    if contact_id is None or contact_id not in _super_admins(bus):
        return None
    try:
        contact = bus.contacts_book.get(contact_id=contact_id)
    except Exception:
        contact = None
    return {
        "contact_id": contact_id,
        # ``0`` means "legacy cookie didn't capture this";
        # callers that need the runtime target should pick
        # one via the assigned-rows table rather than
        # trust this value.
        "magi_id": 0,
        "tgid": int(contact.tgid) if contact is not None and contact.tgid is not None else None,
        "display_name": contact.display_name if contact is not None else None,
        "admin": bool(contact.admin) if contact is not None else False,
        "assigned": False,
        "source": "legacy",
    }


def _super_admins(bus: Bus) -> set[int]:
    """Read the WebUI-admin allowlist as a set of contact ids.

    The identity is the **contact (contact_id), not the per-channel
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
        return (
            {admin.contact_id for admin in admins.list_for_magis(magis_id=1)} if admins else set()
        )
    contacts = bus.contacts_book.list_admins()
    return {contact.id for contact in contacts}


# -- request / response schemas -----------------------------------------


class AllowedLoginAccount(BaseModel):
    """One row in the login-page dropdown.

    ``contact_id`` is the cross-channel identity. ``name`` is the
    contact's display name (shown in the dropdown).
    """

    contact_id: int
    name: str
    display_name: str | None = None


class AllowedLoginAccountsResponse(BaseModel):
    accounts: list[AllowedLoginAccount]


class SendLoginCodeRequest(BaseModel):
    # UID is the cross-channel identity; the server
    # resolves the UID's bound IM (TG for now) and
    # delivers the code through it. The wire format is
    # intentionally contact_id-only so adding Slack / future IMs
    # is purely a server-side change.
    contact_id: int


class SendLoginCodeResponse(BaseModel):
    ok: bool
    expires_in: int = 0
    error: str | None = None


class VerifyLoginCodeRequest(BaseModel):
    contact_id: int
    code: str = Field(min_length=6, max_length=6)
    # The MAGI the operator picked on LandingPage — written
    # into the v3 session cookie so /me and the runtime proxy
    # know which MAGI this session is bound to without a
    # second round-trip.
    magi_id: int = 0


class VerifyLoginCodeResponse(BaseModel):
    ok: bool
    error: str | None = None


class MeResponse(BaseModel):
    """Response of ``/me``.

    D.24: the cookie is keyed by ``contact_id`` (the cross-
    channel identity), not the per-channel delivery
    address. The response surfaces the contact identity
    so the frontend can display it directly; the
    ``tgid`` is the bound TG chat id (may be
    ``None`` for admins who never bound a TG bot) and
    is exposed for any future "send to my TG"
    affordance.
    """

    contact_id: int
    tgid: int | None = None
    display_name: str | None = None
    admin: bool = True  # sourced from the contact row; pre-2024 was a hardcoded True
    assigned: bool = False
    selected_magi_id: int | None = None
    # ``login_methods`` + ``password_set`` mirror the
    # local Contact password hash + the bound IM. The
    # Settings → Security card renders the form from
    # these; the LoginPage uses ``/api/auth/login-methods``
    # so it doesn't need a valid session.
    login_methods: list[str] = []
    password_set: bool = False

    # D.24: the cookie is keyed by ``contact_id``, not
    # the per-channel delivery address. The response
    # surfaces the contact identity so the frontend
    # can display it directly; the ``tgid`` is
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


def _load_login_code(bus: Bus, contact_id: int) -> dict | None:
    raw = (
        control_store.get(bus, f"{_LOGIN_KEY}.{contact_id}")
        if control_store.enabled()
        else bus.settings_book.get(key=f"{_LOGIN_KEY}.{contact_id}")
    )
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


def _store_login_code(
    bus: Bus, contact_id: int, code: str, issued_at: datetime, expires_at: float
) -> None:
    value = json.dumps(
        {
            "code": code,
            "issued_at": issued_at.replace(microsecond=0).isoformat(),
            "expires_at": expires_at,
            "last_sent_at": issued_at.timestamp(),
        }
    )
    if control_store.enabled():
        control_store.set(bus, f"{_LOGIN_KEY}.{contact_id}", value)
    else:
        bus.settings_book.set(key=f"{_LOGIN_KEY}.{contact_id}", value=value)


def _clear_login_code(bus: Bus, contact_id: int) -> None:
    if control_store.enabled():
        control_store.delete(bus, f"{_LOGIN_KEY}.{contact_id}")
    else:
        bus.settings_book.delete(key=f"{_LOGIN_KEY}.{contact_id}")


# -- endpoints ---------------------------------------------------------


class AvailableMAGI(BaseModel):
    id: int
    name: str | None = None


class AvailableMAGIResponse(BaseModel):
    magi: list[AvailableMAGI]


class TargetLoginRequest(BaseModel):
    contact_id: int
    role: str = "assigned"


class TargetVerifyRequest(TargetLoginRequest):
    code: str = Field(min_length=6, max_length=6)


async def _target_access(
    bus: Bus, magi_id: int, method: str, path: str, payload: dict[str, object] | None = None
) -> dict[str, Any]:
    """Call a target runtime before a browser identity exists.

    This is still authenticated service-to-service: ``operator_id=0`` marks
    the narrow pre-login capability, and the signature remains bound to the
    selected runtime id and exact path.
    """
    try:
        runtime = bus.runtime_state_book.get(runtime_id=magi_id) if bus.runtime_state_book else None
        base = getattr(runtime, "base_url", None) if runtime else None
        if not base:
            raise RuntimeError("runtime unavailable")
    except RuntimeError as exc:
        raise MagiHTTPException(
            503, "access.runtime_unreachable", "Selected MAGI runtime is unreachable"
        ) from exc
    headers = build_proxy_headers(
        method=method,
        path_and_query=path,
        target_id=magi_id,
        operator_id=0,
        operator_name="WebUI login",
        tgid=None,
    )
    try:
        async with httpx.AsyncClient(timeout=CONTROL_TIMEOUT) as client:
            response = await client.request(method, base + path, json=payload, headers=headers)
    except httpx.HTTPError as exc:
        raise MagiHTTPException(
            503, "access.runtime_unreachable", "Selected MAGI runtime is unreachable"
        ) from exc
    try:
        body = response.json()
    except ValueError:
        body = {"detail": "Selected MAGI returned an invalid response"}
    if response.is_error:
        raise MagiHTTPException(
            response.status_code,
            str(body.get("code", "access.target_error")),
            str(body.get("detail", "Target request failed")),
        )
    return body


@router.get("/available-magi", response_model=AvailableMAGIResponse)
async def available_magi(bus: BusDep) -> AvailableMAGIResponse:
    """List login targets that are currently running.

    The control deployment reads runtime registry metadata only.  It does not
    read a target's local workspace or user records.
    """
    runtimes_book = bus.runtime_state_book
    if runtimes_book is None:
        return AvailableMAGIResponse(magi=[])
    running_states = {
        RuntimeObservedState.STARTING,
        RuntimeObservedState.STARTED,
    }
    targets = [
        AvailableMAGI(id=rt.runtime_id, name=rt.backend_ref)
        for rt in runtimes_book.list_all()
        if rt.observed_state in running_states
    ]
    return AvailableMAGIResponse(magi=targets)


@router.get("/targets/{magi_id}/accounts")
async def target_accounts(magi_id: int, bus: BusDep) -> dict[str, object]:
    """Unified login-accounts picker for the selected MAGI.

    The runtime only sees the per-MAGI assigned users in its
    local SQLite; Genesis admins live in the webui's MAGIS DB.
    We proxy to the runtime for assigned, then merge in the
    webui's ``magis_admins`` so the picker offers every
    identity this MAGI accepts — including the same person
    appearing under both ``admin`` and ``assigned`` scopes.
    """
    runtime_view = await _target_access(bus, magi_id, "GET", "/api/access/login-accounts")
    runtime_accounts = list(runtime_view.get("accounts", []) or [])
    admin_accounts = _admin_accounts_for_magi(bus, magi_id)
    merged = _merge_login_accounts(runtime_accounts, admin_accounts)
    return {
        "magi_id": runtime_view.get("magi_id", magi_id),
        "magis_id": runtime_view.get("magis_id"),
        "accounts": merged,
    }


def _admin_accounts_for_magi(bus: Bus, magi_id: int) -> list[dict[str, object]]:
    """Return admin-side picker rows for ``magi_id``'s MAGIS.

    The webui owns the MAGIS DB; the runtime does not. For
    each ``magis_admins`` row we emit one picker row keyed
    by the runtime-local ``contact_id`` (opaque int). The
    display name falls back to ``"Admin #<id>"`` when the
    webui cannot resolve the runtime contact's display_name
    (which it normally cannot — the contacts live in the
    runtime's local SQLite). The runtime's own picker row
    for the same ``contact_id`` + ``role="admin"`` is
    de-duplicated by :func:`_merge_login_accounts`.
    """
    if bus.magis_admins_book is None or bus.magis_admins_book is None:
        return []
    root = bus.magis_book.get_root() if bus.magis_book else None
    if root is None:
        return []
    out: list[dict[str, object]] = []
    for admin in bus.magis_admins_book.list_for_magis(magis_id=root.id):
        out.append(
            {
                "contact_id": admin.contact_id,
                "name": f"Admin #{admin.contact_id}",
                "role": "admin",
                "admin": True,
                "assigned": False,
                "has_password": True,
                "has_tg_code": False,
                "tgid": None,
            }
        )
    return out


def _merge_login_accounts(
    runtime_accounts: list[dict[str, object]],
    admin_accounts: list[dict[str, object]],
) -> list[dict[str, object]]:
    """Combine the runtime's picker with webui admin rows.

    ``(contact_id, role)`` is the dedup key. Admin rows from
    the webui fill in any admin slot the runtime missed
    (typical when the runtime doesn't have MAGIS DB access).
    """
    merged: dict[tuple[int, str], dict[str, object]] = {}

    def _int(raw: object) -> int:
        # The picker shape guarantees ints here; the cast
        # through ``object`` only exists because the row
        # type is ``dict[str, object]``.
        return int(raw)  # type: ignore[arg-type]

    def _str(raw: object) -> str:
        return str(raw)  # type: ignore[arg-type]

    for row in runtime_accounts:
        key = (_int(row.get("contact_id", 0)), _str(row.get("role", "assigned")))
        merged[key] = dict(row)
    for row in admin_accounts:
        key = (_int(row.get("contact_id", 0)), _str(row.get("role", "admin")))
        if key in merged:
            existing = merged[key]
            for field in ("admin", "assigned", "has_password", "has_tg_code"):
                if row.get(field):
                    existing[field] = True
            if not existing.get("name") and row.get("name"):
                existing["name"] = row["name"]
        else:
            merged[key] = dict(row)
    out = list(merged.values())
    out.sort(key=lambda r: (_str(r.get("role", "assigned")), _str(r.get("name", "")).lower(), _int(r.get("contact_id", 0))))
    return out


@router.post("/targets/{magi_id}/send-login-code")
async def target_send_login_code(
    magi_id: int, payload: TargetLoginRequest, bus: BusDep
) -> dict[str, object]:
    return await _target_access(
        bus, magi_id, "POST", "/api/access/send-login-code", payload.model_dump()
    )


@router.post("/targets/{magi_id}/verify-login-code", response_model=VerifyLoginCodeResponse)
async def target_verify_login_code(
    magi_id: int, payload: TargetVerifyRequest, response: Response, bus: BusDep
) -> VerifyLoginCodeResponse:
    result = await _target_access(
        bus, magi_id, "POST", "/api/access/verify-login-code", payload.model_dump()
    )
    if not result.get("ok"):
        return VerifyLoginCodeResponse(
            ok=False, error=str(result.get("error") or "Code does not match")
        )
    # The identity is the contact_id; the tgid is optional
    # colour (a WebUI-only operator has none). Validating the
    # tgid here instead would reject a legitimate password- or
    # WebUI-bound account as an "invalid identity".
    contact_id = result.get("contact_id")
    if not isinstance(contact_id, int):
        raise MagiHTTPException(
            502, "access.target_invalid_identity", "Selected MAGI returned an invalid identity"
        )
    tgid = result.get("tgid")
    raw_display_name = result.get("display_name")
    display_name = raw_display_name if isinstance(raw_display_name, str) else None
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=_sign_selected_session(
            bus,
            magi_id=magi_id,
            contact_id=contact_id,
            tgid=_as_optional_int(tgid),
            display_name=display_name,
            admin=bool(result.get("admin")),
            assigned=bool(result.get("assigned")),
        ),
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        path="/",
    )
    return VerifyLoginCodeResponse(ok=True)


@router.get("/allowed-accounts", response_model=AllowedLoginAccountsResponse)
async def list_allowed_accounts(bus: BusDep) -> AllowedLoginAccountsResponse:
    """The list of UIDs that can log in to ADAM.

    UID is the row's identity; the dropdown's primary key is
    the UID, not the IM chat id. We still include
    ``tgid`` in the response so the frontend can show
    a contact as e.g. "EVA-000 (Telegram: 9999001)"
    — useful when two contacts share a display name — but
    the wire-protocol ask for the verification code takes the
    UID, not a chat id.

    Filter: admin rows that have at least one bound IM
    (today: ``tgid IS NOT NULL``). An admin who never
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
    # each contact_id → its bound TG chat id so the frontend
    # can show the chat hint next to the display name.
    # Admins without a TG binding are dropped (no IM to
    # deliver the verification code through).
    admin_contact_ids = _super_admins(bus)
    candidates: dict[int, tuple[int | None, str | None]] = {}
    if admin_contact_ids:
        for contact_id in admin_contact_ids:
            contact = bus.contacts_book.get(contact_id=contact_id)
            if contact is None or contact.tgid is None:
                continue
            candidates[contact.id] = (
                contact.tgid,
                contact.display_name or contact.name,
            )

    for contact_id, (_tgid_int, fallback_name) in sorted(candidates.items()):
        accounts.append(
            AllowedLoginAccount(
                contact_id=contact_id,
                name=fallback_name or f"contact_id {contact_id}",
            )
        )

    # 2. Assigned contacts. C0: the contacts / evas tables don't
    # exist yet, so this list is always empty. The query is
    # sketched in the comment so C1.1 / C6 can drop it in.
    #
    #   SELECT e.contact_id, e.tgid, e.name
    #   FROM contacts e
    #   JOIN evas v ON v.contact_id = e.id
    #   WHERE e.tgid IS NOT NULL
    #     AND v.status != 'shutting_down'
    #     AND NOT EXISTS (
    #       SELECT 1 FROM contacts a
    #       WHERE a.contact_id = e.contact_id AND a.role = 'admin'
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
    """Send a 6-digit code to ``contact_id`` for login.

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
    contact_id = payload.contact_id

    if contact_id not in _super_admins(bus):
        # Anti-enumeration: respond as if we sent, but no-op
        # behind the scenes. The frontend shows the same
        # "code sent" UX so an attacker can't probe which
        # contact_ids are admins.
        logger.info(
            "login-code send: contact_id is not a super_admin; suppressed",
            extra={"contact_id": contact_id},
        )
        return SendLoginCodeResponse(ok=True, expires_in=_CODE_TTL_SECONDS)

    if control_store.enabled():
        return SendLoginCodeResponse(ok=False, error="control-plane login is unavailable")
    # Contact is the single source of truth for the current Telegram binding.
    # Resolve it before recording a login code, so an unbound operator cannot
    # accumulate credentials that were never delivered.
    contact = bus.contacts_book.get(contact_id=contact_id)
    if contact is None or contact.tgid is None:
        return SendLoginCodeResponse(
            ok=False,
            error="This account has no IM channel configured; "
            "ask the deployer to bind a TG chat first.",
        )

    # Cooldown — same anti-abuse logic as the admin code.
    # Storage is keyed by UID so a future second-IM
    # binding doesn't leave half-stored codes behind.
    previous = _load_login_code(bus, contact_id)
    if previous:
        try:
            prev_sent_at = float(previous.get("last_sent_at", 0))
        except (TypeError, ValueError):
            prev_sent_at = 0
        if prev_sent_at:
            elapsed = datetime.now(UTC).timestamp() - prev_sent_at
            if elapsed < _RESEND_COOLDOWN_SECONDS:
                remaining = int(_RESEND_COOLDOWN_SECONDS - elapsed)
                return SendLoginCodeResponse(
                    ok=False,
                    error=f"Wait {remaining}s before requesting a new code.",
                )

    code = _generate_code()
    issued_at = datetime.now(UTC)
    expires_at = issued_at.timestamp() + _CODE_TTL_SECONDS
    _store_login_code(bus, contact_id, code, issued_at, expires_at)

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
                destination=str(contact.tgid),
                payload={"text": code_text, "contact_id": contact_id},
            )
        )
    except Exception as exc:
        _clear_login_code(bus, contact_id)
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
    cookie (contact_id) on success.
    """
    contact_id = payload.contact_id
    code = payload.code.strip()
    if not code.isdigit() or len(code) != 6:
        return VerifyLoginCodeResponse(ok=False, error="Code must be 6 digits")

    if contact_id not in _super_admins(bus):
        # Anti-enumeration: same response as a wrong code.
        return VerifyLoginCodeResponse(ok=False, error="Code does not match")

    stored = _load_login_code(bus, contact_id)
    if not stored:
        return VerifyLoginCodeResponse(
            ok=False,
            error="No code sent to this contact_id — request a new one.",
        )

    try:
        expires_at = float(stored.get("expires_at", 0))
    except (TypeError, ValueError):
        expires_at = 0
    if not expires_at or datetime.now(UTC).timestamp() >= expires_at:
        _clear_login_code(bus, contact_id)
        return VerifyLoginCodeResponse(ok=False, error="Code expired — request a new one.")

    # Burn on any path past expiry (mismatch, success, anything) so
    # the code can't be retried.
    _clear_login_code(bus, contact_id)

    if str(stored.get("code", "")) != code:
        return VerifyLoginCodeResponse(ok=False, error="Code does not match")

    # Look up the contact so we can stamp display_name +
    # admin + tgid into the v3 cookie payload.
    # The ``contact_id`` here is the cross-channel
    # identity (Contact PK), so ``get(contact_id=)``
    # returns the row directly.
    contact_row = bus.contacts_book.get(contact_id=contact_id)
    if contact_row is None:
        # The cookie row was deleted between send and
        # verify. Treat as auth failure rather than
        # crashing the login flow.
        return VerifyLoginCodeResponse(
            ok=False, error="contact not found"
        )

    # Sign in: set the v4 session cookie so /me and
    # the runtime proxy both recognise the session in
    # control-plane mode. The legacy ``_sign_contact_id``
    # cookie is no longer issued by any login endpoint —
    # legacy cookies still validate via ``resolve_session``
    # for in-flight transitions only.
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=_sign_selected_session(
            bus,
            magi_id=payload.magi_id,
            contact_id=contact_id,
            tgid=_as_optional_int(contact_row.tgid),
            display_name=contact_row.display_name,
            admin=bool(contact_row.admin),
            # TG-code login alone doesn't bind the operator
            # to a specific runtime; the runtime proxy
            # consults the assigned-row table to decide
            # which MAGI to forward to.
            assigned=False,
        ),
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        # path="/" so every endpoint sees it
        path="/",
    )
    logger.info(
        "user signed in",
        extra={"contact_id": contact_id, "magi_id": payload.magi_id},
    )
    return VerifyLoginCodeResponse(ok=True)


@router.post("/logout", status_code=204)
async def logout(response: Response) -> Response:
    """Clear the session cookie. The endpoint always returns 204 even
    if the user wasn't signed in — logout is idempotent."""
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return Response(status_code=204)


async def _login_methods_via_runtime(
    bus: Bus, magi_id: int, contact_id: int
) -> list[str] | None:
    """Control-plane: ask the runtime which methods ``contact_id`` has.

    The singleton WebUI cannot answer this itself — ``contacts``
    and the password hashes live in the runtime's local store,
    not in the MAGIS store its Bus is bound to. The runtime
    already computes both flags for the login picker, so this
    reuses ``/access/login-accounts`` rather than adding a
    second source of truth that could drift from it.

    Returns ``None`` when the runtime is unreachable or does not
    know the contact, so the caller can fall back to reporting
    nothing rather than claiming the operator has no password.
    """
    try:
        result = await _target_access(bus, magi_id, "GET", "/api/access/login-accounts")
    except Exception:
        logger.exception("me: login-methods lookup failed for contact_id=%s", contact_id)
        return None
    accounts = result.get("accounts")
    if not isinstance(accounts, list):
        return None
    for row in accounts:
        # The same contact appears once per role (admin +
        # assigned); both rows carry identical credential
        # flags, so the first match is enough.
        if isinstance(row, dict) and row.get("contact_id") == contact_id:
            methods: list[str] = []
            if row.get("has_password"):
                methods.append("password")
            if row.get("has_tg_code"):
                methods.append("tg_code")
            return methods
    return None


@router.get("/me", response_model=MeResponse)
async def me(
    bus: BusDep,
    magi_session: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None,
) -> MeResponse:
    """Return the current user, or 401 if no valid session.

    "Valid" means: the cookie's contact_id resolves to a
    row in ``contacts`` with role='admin'. D.24 — the
    cookie is the contact_id (cross-channel identity),
    not the chat id the user typed on the login page.
    The ``tgid`` field in the response is the
    bound TG chat id, looked up from the same row.

    Two data sources, one per deployment mode. A single-MAGI
    runtime reads its own ``contacts``. The control plane
    cannot: its Bus is bound to the MAGIS store, which has no
    ``contacts`` table, so it asks the selected runtime instead
    — otherwise ``login_methods`` / ``password_set`` come back
    empty and the Settings → Security card tells an operator who
    does have a password that they have none.
    """
    session = resolve_session(bus, magi_session)
    if session is None:
        raise MagiHTTPException(status_code=401, code="auth.not_signed_in", detail="Not signed in")
    contact_id = int(session["contact_id"])
    selected_magi_id = int(session["magi_id"]) if session.get("magi_id") else None

    if control_store.enabled():
        methods = (
            await _login_methods_via_runtime(bus, selected_magi_id, contact_id)
            if selected_magi_id
            else None
        ) or []
        return MeResponse(
            contact_id=contact_id,
            tgid=session.get("tgid"),
            display_name=session.get("display_name"),
            admin=bool(session.get("admin")),
            assigned=bool(session.get("assigned")),
            selected_magi_id=selected_magi_id,
            login_methods=methods,
            password_set="password" in methods,
        )

    # Re-read the row to surface login_methods +
    # password_set for the Settings → Security card.
    # If the row has since been deleted (admin removed
    # mid-session), fall back to cookie-stamped fields.
    try:
        contact = bus.contacts_book.get(contact_id=contact_id)
        if contact is None:
            return MeResponse(
                contact_id=contact_id,
                tgid=session.get("tgid"),
                display_name=session.get("display_name"),
                admin=bool(session.get("admin")),
                assigned=bool(session.get("assigned")),
                selected_magi_id=selected_magi_id,
            )
        methods, _is_webui_only = _login_methods_for(bus, contact_id)
        return MeResponse(
            contact_id=contact.id,
            tgid=contact.tgid,
            display_name=contact.name,
            admin=bool(contact.admin),
            assigned=bool(session.get("assigned")),
            selected_magi_id=selected_magi_id,
            login_methods=methods,
            password_set="password" in methods,
        )
    except Exception:
        # Defensive: a transient DB hiccup shouldn't
        # lock the operator out of ``/me``. We saw the
        # cookie is valid (contact_id in _super_admins); the
        # metadata lookup is best-effort.
        logger.exception("me: contact lookup failed for contact_id=%s", contact_id)
        return MeResponse(
            contact_id=contact_id,
            tgid=session.get("tgid"),
            display_name=session.get("display_name"),
            admin=bool(session.get("admin")),
            assigned=bool(session.get("assigned")),
            selected_magi_id=selected_magi_id,
        )


# -- Password login + credential management -------------------------------
#
# Four endpoints plus a helper used by the LoginPage to
# pick which tab to render. The TG code flow above is
# unchanged; password login is a parallel route that
# co-exists with the code route so an operator can switch
# back and forth. The 60s cooldown is shared per-contact_id
# (success or failure both reset the timer) so a typo
# followed by a successful retry still has to wait a
# minute — same UX as the TG code path.


class LoginMethodsResponse(BaseModel):
    """Which login methods are available for ``contact_id``.

    The frontend uses this to decide whether to show a
    "password" tab, a "TG code" tab, or both. Steered by
    the local :class:`ContactBook` password hash on the single-MAGI
    path and the ``magis_admins`` row (bound Telegram
    chat id) on the control-plane path.
    """

    contact_id: int
    methods: list[str] = []
    # True when the user has a Contact row with no
    # tgid — i.e. they're a WebUI-only operator.
    is_webui_only: bool = False


class LoginPasswordRequest(BaseModel):
    contact_id: int
    # "admin" picks a Genesis admin scope (cookie admin=True);
    # "assigned" picks the per-MAGI scope (cookie assigned=True).
    # The same contact can appear under both, letting the
    # operator pick which layer they want to log in as.
    role: str = "assigned"
    password: str
    # The MAGI the operator picked on LandingPage — written
    # into the v3 session cookie (see ``_sign_selected_session``).
    magi_id: int = 0


class LoginPasswordResponse(BaseModel):
    ok: bool
    error: str | None = None
    # ``retry_after`` is set when the cooldown is active
    # so the frontend can disable the button for the
    # remaining seconds instead of letting the user spam
    # retries.
    retry_after: int | None = None


class SetPasswordRequest(BaseModel):
    contact_id: int
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


def _password_hash(bus: Bus, contact_id: int) -> str | None:
    """Return the local password credential without exposing persistence."""
    return bus.contacts_book.get_password_hash(contact_id=contact_id)


def _set_password_hash(bus: Bus, contact_id: int, secret_hash: str) -> None:
    bus.contacts_book.set_password_hash(
        contact_id=contact_id,
        password_hash=secret_hash,
    )


def _delete_password_hash(bus: Bus, contact_id: int) -> bool:
    return bus.contacts_book.clear_password_hash(contact_id=contact_id)


def _login_methods_for(bus: Bus, contact_id: int) -> tuple[list[str], bool]:
    """Return ``(methods, is_webui_only)`` for ``contact_id``.

    ``contact_id`` is always the runtime-local ``contacts.id``
    — the same identity ``/me`` reads out of the session
    cookie. Both available methods are reported: a contact
    with a password but no TG binding is a first-class
    operator (the onboarding wizard can set an admin
    password before any bot is configured), so the absence
    of a ``tgid`` must never read as "no way to sign in".

    Not usable from the control plane. The singleton WebUI's
    Bus is bound to the MAGIS store, which has no ``contacts``
    table (``contacts`` is local scope) — every read here
    raises there. An earlier control-plane branch looked the
    operator up with ``get_by_telegram()``, treating this
    argument as a TG chat id; it could not have worked for
    that reason, and it also ignored passwords entirely.
    The control-plane source of truth is the picker's
    ``has_password`` / ``has_tg_code`` (see
    ``/access/login-accounts``), which the runtime computes
    against its own local store. The guard below keeps that
    deployment mode degrading to "nothing to report" instead
    of a 500.
    """
    try:
        contact = bus.contacts_book.get(contact_id=contact_id)
        if contact is None:
            return ([], True)
        methods: list[str] = []
        if _password_hash(bus, contact_id):
            methods.append("password")
        is_webui_only = True
        if contact.tgid is not None:
            methods.append("tg_code")
            is_webui_only = False
        return (methods, is_webui_only)
    except Exception:
        logger.exception("login methods lookup failed for contact_id=%s", contact_id)
        return ([], True)


@router.get("/login-methods", response_model=LoginMethodsResponse)
async def login_methods(contact_id: int, bus: BusDep) -> LoginMethodsResponse:
    """Public probe — returns the available login methods for ``contact_id``.

    Used by the LoginPage to decide which tabs to render.
    Anti-enumeration: returns ``methods=[]`` rather than
    404 when the contact_id doesn't exist (the LoginPage then
    shows "no login methods" and the operator can ping
    the admin). A future tightening can flip this to a
    404 once the WebUI treats contact_id as public knowledge.
    """
    methods, webui_only = _login_methods_for(bus, contact_id)
    return LoginMethodsResponse(
        contact_id=contact_id,
        methods=methods,
        is_webui_only=webui_only,
    )


async def _login_password_via_runtime(
    bus: Bus,
    payload: LoginPasswordRequest,
    response: Response,
    *,
    magi_id: int,
) -> LoginPasswordResponse:
    """Webui control-plane path — forward to the runtime.

    The runtime owns the password_hash in its local SQLite.
    The webui calls ``/api/access/login-password`` (which
    always requires ``_require_webui`` HMAC auth — it never
    reaches the browser directly) and uses the runtime's
    authoritative response to set the cookie.
    """
    upstream = await _target_access(
        bus,
        magi_id or 0,
        "POST",
        "/api/access/login-password",
        {
            "contact_id": payload.contact_id,
            "role": payload.role,
            "password": payload.password,
        },
    )
    if not upstream.get("ok"):
        return LoginPasswordResponse(
            ok=False,
            error=str(upstream.get("error") or "Sign-in failed"),
            retry_after=_as_optional_int(upstream.get("retry_after")),
        )
    # ``upstream`` is another service's JSON, so coerce rather
    # than trust: a non-int tgid would sign cleanly and then
    # fail ``selected_session``'s type check on every request,
    # locking the operator into a 401 that re-logging-in cannot
    # clear (each attempt mints the same unusable cookie).
    raw_tgid = upstream.get("tgid")
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=_sign_selected_session(
            bus,
            magi_id=magi_id,
            contact_id=int(upstream.get("contact_id") or payload.contact_id),
            tgid=_as_optional_int(raw_tgid),
            display_name=upstream.get("display_name"),
            admin=bool(upstream.get("admin")),
            assigned=bool(upstream.get("assigned")),
        ),
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        path="/",
    )
    logger.info(
        "user signed in via password (via runtime)",
        extra={
            "contact_id": payload.contact_id,
            "role": payload.role,
            "magi_id": magi_id,
        },
    )
    return LoginPasswordResponse(ok=True)


async def _login_password_local(
    bus: Bus,
    payload: LoginPasswordRequest,
    response: Response,
) -> LoginPasswordResponse:
    """Runtime-only path — verify the password against this Bus's local SQLite."""
    if payload.contact_id not in _super_admins(bus):
        return LoginPasswordResponse(ok=False, error="password does not match")

    from magi.channels.api import password_utils

    if not password_utils.check_cooldown(
        bus,
        payload.contact_id,
        cooldown_seconds=_RESEND_COOLDOWN_SECONDS,
    ):
        record = password_utils._store_get(bus, payload.contact_id) or {}
        last = float(record.get("last_attempt_at", 0))
        remaining = max(1, int(_RESEND_COOLDOWN_SECONDS - (_now_ts() - last)))
        return LoginPasswordResponse(
            ok=False,
            error=f"Wait {remaining}s before trying again.",
            retry_after=remaining,
        )

    password_utils.record_attempt(bus, payload.contact_id)

    stored = _password_hash(bus, payload.contact_id)
    if not stored or not password_utils.verify_password(stored, payload.password):
        return LoginPasswordResponse(
            ok=False,
            error="password does not match",
            retry_after=_RESEND_COOLDOWN_SECONDS,
        )

    password_utils.clear_attempt(bus, payload.contact_id)
    contact_row = bus.contacts_book.get(contact_id=payload.contact_id)
    if contact_row is None:
        return LoginPasswordResponse(ok=False, error="contact not found")
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=_sign_selected_session(
            bus,
            magi_id=payload.magi_id,
            contact_id=payload.contact_id,
            tgid=_as_optional_int(contact_row.tgid),
            display_name=contact_row.display_name,
            admin=payload.role == "admin",
            assigned=payload.role == "assigned" and contact_row.role == "assigned",
        ),
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        path="/",
    )
    logger.info(
        "user signed in via password",
        extra={"contact_id": payload.contact_id, "role": payload.role, "magi_id": payload.magi_id},
    )
    return LoginPasswordResponse(ok=True)


@router.post("/login-password", response_model=LoginPasswordResponse)
async def login_password(
    payload: LoginPasswordRequest,
    response: Response,
    bus: BusDep,
) -> LoginPasswordResponse:
    """Verify password and set the session cookie.

    The webui (control-plane) has no local contacts_book for
    password hashes; it forwards the verify to the runtime
    via the existing ``_target_access`` proxy and trusts the
    runtime's authoritative response. The runtime path
    enforces the 60s cooldown, verifies the scrypt hash,
    and looks up the contact's role for the cookie flags.

    On success the cookie carries ``admin = (role == "admin")``
    (cookie layer) plus ``assigned = (role == "assigned" and
    contact.role == "assigned")``. The webui re-validates
    ``admin`` on every request via
    :func:`_super_admins` so the cookie flag is a hint, not
    the source of truth.

    Anti-enumeration: a contact_id that the picker did not
    offer (and therefore has no password_hash) responds as
    "ok" but never sets a cookie.
    """
    if not payload.contact_id or not isinstance(payload.contact_id, int):
        return LoginPasswordResponse(ok=False, error="invalid contact_id")
    if not payload.password:
        return LoginPasswordResponse(ok=False, error="password required")

    if control_store.enabled():
        return await _login_password_via_runtime(
            bus, payload, response, magi_id=payload.magi_id
        )

    return await _login_password_local(bus, payload, response)


def _now_ts() -> float:
    return datetime.now(UTC).timestamp()


@router.post("/set-password", response_model=SetPasswordResponse)
async def set_password(
    payload: SetPasswordRequest,
    request: Request,
    _admin: AdminGate,
) -> SetPasswordResponse:
    """Set or replace a user's password.

    Both flows go through one endpoint:

      - **Self-service**: ``contact_id`` is the calling user's
        contact_id, ``old_password`` is required. The ``AdminGate``
        ensures the caller is signed in; the body check
        proves they own the row.
      - **Admin override**: an admin can set a password
        for any ``contact_id`` without ``old_password``. Used
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

    if not payload.contact_id or not isinstance(payload.contact_id, int):
        return SetPasswordResponse(ok=False, error="invalid contact_id")

    caller_session = resolve_session(
        bus,
        request.cookies.get(SESSION_COOKIE_NAME),
    )
    if caller_session is None:
        return SetPasswordResponse(ok=False, error="not signed in")
    caller_contact_id = int(caller_session["contact_id"])

    if caller_contact_id != payload.contact_id:
        # Admin override — AdminGate already proved the
        # caller is admin, so we skip the old_password
        # check.
        caller_contact = bus.contacts_book.get(contact_id=caller_contact_id)
        if caller_contact is None or not caller_contact.admin:
            return SetPasswordResponse(
                ok=False,
                error="only admins can set another user's password",
            )
    else:
        # Self-service: must know the existing password,
        # unless they're setting one for the first time.
        existing = _password_hash(bus, payload.contact_id)
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

    _set_password_hash(bus, payload.contact_id, new_hash)
    logger.info("password set", extra={"contact_id": payload.contact_id, "by": caller_contact_id})
    return SetPasswordResponse(ok=True)


@router.post("/change-password", response_model=SetPasswordResponse)
async def change_password(
    payload: ChangePasswordRequest,
    request: Request,
) -> SetPasswordResponse:
    """Self-service: change the signed-in user's password.

    Thin wrapper around :func:`set_password` that always
    uses the caller's contact_id as the target and requires
    ``old_password`` unconditionally. The frontend hits
    this from the Settings → Security card; the unified
    :func:`set_password` endpoint serves the admin
    override flow.
    """
    bus = get_bus(request)
    from magi.channels.api import password_utils

    caller_session = resolve_session(
        bus,
        request.cookies.get(SESSION_COOKIE_NAME),
    )
    if caller_session is None:
        return SetPasswordResponse(ok=False, error="not signed in")
    caller_contact_id = int(caller_session["contact_id"])
    try:
        new_hash = password_utils.hash_password(payload.new_password)
    except ValueError as exc:
        return SetPasswordResponse(ok=False, error=str(exc))

    existing = _password_hash(bus, caller_contact_id)
    if existing is None:
        return SetPasswordResponse(
            ok=False,
            error="no password set yet — use set-password instead",
        )
    if not password_utils.verify_password(existing, payload.old_password):
        return SetPasswordResponse(ok=False, error="old_password does not match")

    _set_password_hash(bus, caller_contact_id, new_hash)
    logger.info("password changed", extra={"contact_id": caller_contact_id})
    return SetPasswordResponse(ok=True)


@router.delete("/credentials/password/{contact_id}", status_code=204)
async def revoke_password(
    contact_id: int,
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
    if not _delete_password_hash(bus, contact_id):
        raise MagiHTTPException(
            status_code=404,
            code="auth.no_password_credential",
            detail=f"contact_id {contact_id} has no password credential",
        )
    logger.info("password revoked", extra={"contact_id": contact_id})
    return Response(status_code=204)
