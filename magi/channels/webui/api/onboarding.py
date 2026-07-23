"""Onboarding API — three-step flow for first-time setup.

    1. Bot token (verify + save)
       ``POST /api/onboarding/verify-bot { token }``
           Calls Telegram's ``getMe``. Returns ``{ok, username}`` or
           ``{ok: false, error}``. **Does not store**.
       ``POST /api/onboarding/save-bot { token, username }``
           Writes the bot token and username into the ``settings`` table.

    2. (implicit / no API) The "Saved" page just displays the
       persisted token + username; the user clicks Next to step 3.

    3. Super admin tgids (verify + save)
       ``POST /api/onboarding/verify-admin { tgid }``
           Sends a connectivity test message to ``tgid`` via the
           saved bot. Returns ``{ok, display_name}`` or ``{ok: false,
           error}``. **Does not store**.
       ``POST /api/onboarding/save-admin { tgids: list[str] }``
           Upserts an ``Employee`` row per tgid with ``role='admin'``,
           ``telegram_id=<tgid>`` and no department. Display names are
           resolved via Telegram ``getChat``. Idempotent.

All four endpoints are read-only or write-only against the ``settings``
table, so they live alongside the webui channel rather than in a future
``magi/adam/`` package.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os

import httpx
from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import select

from magi.agent.db import require_state_dir

logger = logging.getLogger("magi.api.onboarding")

router = APIRouter(tags=["onboarding"])

# 5s is generous for a single Telegram call.
_TELEGRAM_TIMEOUT_SECONDS = 5.0


def _state_dir() -> str:
    """Read MAGI_STATE_DIR each call — keeps state_dir testable + env-friendly."""
    return require_state_dir()


# -- request / response schemas -----------------------------------------


class VerifyBotRequest(BaseModel):
    token: str = Field(min_length=1, max_length=200)


class VerifyBotResponse(BaseModel):
    ok: bool
    username: str | None = None
    error: str | None = None


class SaveBotRequest(BaseModel):
    token: str = Field(min_length=1, max_length=200)
    username: str = Field(min_length=1, max_length=100)


class SaveBotResponse(BaseModel):
    ok: bool
    error: str | None = None


class VerifyAdminRequest(BaseModel):
    tgid: str = Field(min_length=1, max_length=64)


class VerifyAdminResponse(BaseModel):
    ok: bool
    display_name: str | None = None
    error: str | None = None


class SendAdminCodeRequest(BaseModel):
    tgid: str = Field(min_length=1, max_length=64)


class SendAdminCodeResponse(BaseModel):
    ok: bool
    expires_in: int = 0  # seconds until the code expires
    error: str | None = None


class VerifyAdminCodeRequest(BaseModel):
    tgid: str = Field(min_length=1, max_length=64)
    code: str = Field(min_length=6, max_length=6)


class VerifyAdminCodeResponse(BaseModel):
    ok: bool
    display_name: str | None = None
    error: str | None = None


class SaveAdminRequest(BaseModel):
    tgids: list[str] = Field(min_length=1)


class SaveAdminResponse(BaseModel):
    ok: bool
    count: int = 0
    error: str | None = None


class OnboardingStatus(BaseModel):
    """Summary of what's already saved. No secrets — token never leaves
    the server. The frontend uses this to skip steps on the wizard."""

    bot_saved: bool
    bot_username: str | None = None
    super_admins_count: int
    super_admins: list[str] = []
    # The single source of truth for "is the wizard done?". Flipped
    # to True only by POST /api/onboarding/complete (the dashboard
    # "OK, got it — sign in →" button). Cleared by /restart. This is
    # deliberately decoupled from ``bot_saved`` and the admin-list
    # fields above so a user who saved a bot but abandoned step 3
    # can still get back into the wizard (and so a deployer can
    # "Restart onboarding" without nuking the saved data).
    onboarding_complete: bool = False


class CompleteRequest(BaseModel):
    pass


class CompleteResponse(BaseModel):
    ok: bool


class RestartRequest(BaseModel):
    pass


class RestartResponse(BaseModel):
    ok: bool


# -- endpoints ---------------------------------------------------------


@router.get("/status", response_model=OnboardingStatus)
async def get_status() -> OnboardingStatus:
    """Read-only summary of the persisted onboarding state.

    The frontend calls this on mount to decide whether to start the
    wizard at step 1 (nothing saved) or skip directly to step 2 / 3
    (bot already saved, optionally with super admins).

    ``onboarding_complete`` is the only field the boot routing
    trusts: it's a strict bool written by ``/complete`` (dashboard
    "OK, got it") and cleared by ``/restart``. Everything else is
    informational / for the wizard's own resume logic.
    """
    from magi.agent.db import Employee, open_session
    from magi.agent.db.settings import state_get

    state_dir = _state_dir()
    bot_username = state_get(state_dir, "telegram.bot_username")

    # Super admins live in the employees table (unified with
    # the rest of the org directory) — that's the single source
    # of truth. The wizard resumes by reading from there so
    # the "you already added N admins" message reflects the
    # canonical state. There's no settings-key fallback by
    # design: a state file pre-C1.x may have a stale
    # ``telegram.super_admins`` key, but the operator can
    # always re-save the admin list to clean that up.
    admins: list[str] = []
    try:
        with open_session() as session:
            for emp in session.scalars(
                select(Employee).where(Employee.role == "admin")
            ).all():
                if emp.telegram_id is not None:
                    admins.append(str(emp.telegram_id))
    except Exception:
        # If the table is unreachable (very early boot) the
        # wizard still loads; admins stays empty until the
        # operator re-saves.
        logger.exception("failed to read admin employees")

    # "True" / "true" / "1" all count. Anything else (including
    # missing) is False. Kept as a plain text flag — the only
    # writer is /complete, which writes the literal "true".
    #
    # The key is ``onboarding.complete`` (not
    # ``telegram.onboarding_complete``) because "is the
    # operator's first-time setup done?" is a system-level
    # state, not a channel-level one. The channel-level keys
    # (``telegram.bot_token``, ``telegram.bot_username``,
    # ``telegram.verify_code.<tgid>``) legitimately carry
    # the ``telegram.`` prefix because the bot identity +
    # chat-id verification ARE Telegram-specific. Onboarding
    # isn't — C5 will onboard Email or Calendar, and that
    # flow's "complete?" flag should live next to this one in
    # the system namespace, not under each channel.
    #
    # Migration: the v0 keys carry ``telegram.`` as a leftover
    # from when bot setup WAS the only onboarding step. Treat
    # the old key as one-shot-equivalent so an operator
    # upgrading from v0 doesn't get sent back into the wizard.
    complete_raw = state_get(state_dir, "onboarding.complete")
    if complete_raw is None:
        # Pre-rename deployments still have the older
        # ``telegram.onboarding_complete`` key. Read it once,
        # migrate forward lazily (don't write here — the
        # wizard's completion will write the new key).
        old_raw = state_get(state_dir, "telegram.onboarding_complete")
        if old_raw is not None:
            logger.info(
                "migrating legacy telegram.onboarding_complete -> onboarding.complete",
                extra={"value": old_raw},
            )
    else:
        old_raw = None
    onboarding_complete = (
        str(complete_raw or old_raw or "").strip().lower() in ("true", "1")
    )

    return OnboardingStatus(
        bot_saved=bool(bot_username),
        bot_username=bot_username,
        super_admins_count=len(admins),
        super_admins=admins,
        onboarding_complete=onboarding_complete,
    )


@router.post("/complete", response_model=CompleteResponse)
async def complete_onboarding(_payload: CompleteRequest) -> CompleteResponse:
    """Mark the wizard as fully complete.

    Called by the dashboard "OK, got it — sign in →" button — i.e.
    only after the user has seen the wizard's result and explicitly
    acknowledged it. Until this endpoint fires, ``/status`` keeps
    reporting ``onboarding_complete=false`` and the boot routing
    keeps sending the user back into the wizard, no matter how
    much of step 1 / 2 / 3 they finished.

    Side effect: stamp a ``llm_credentials_missing`` action item
    onto every current admin so the dashboard's Action Items
    pane nudges each operator to set their LLM provider + key
    before chatting. The first onboard is the natural moment
    for this (each admin's row already exists by the time the
    wizard reaches step 4); re-onboarding later (after
    ``/restart``) re-runs the same logic against whatever the
    admin set is now.

    The action-item insert runs **before** the
    ``onboarding_complete`` flag is written so a partial
    failure can't leave the user at the dashboard with the
    flag set and no nudges. If the insert fails we report
    ``ok=false`` and the wizard's button shows the error —
    the user retries, the helper is idempotent so no
    duplicate rows on retry.
    """
    from sqlalchemy import select

    from magi.channels.webui.api.action_items import (
        _ensure_llm_credentials_item,
    )
    from magi.agent.db import Employee, open_session
    from magi.agent.db.settings import state_set

    # 1. Stamp one nudge per current admin. Helper is
    #    idempotent — re-running (e.g. retry after failure,
    #    second wizard pass after /restart) is a no-op for any
    #    admin that already has an open row.
    try:
        with open_session() as session:
            admins = list(
                session.scalars(
                    select(Employee).where(Employee.role == "admin")
                ).all()
            )
            inserted = 0
            for admin in admins:
                if _ensure_llm_credentials_item(session, admin.id):
                    inserted += 1
            session.commit()
    except Exception as exc:  # pragma: no cover — DB failure
        logger.exception(
            "complete: action-item insert failed (%d admins, %d inserted before error)",
            len(admins) if 'admins' in locals() else 0, inserted if 'inserted' in locals() else 0,
        )
        return CompleteResponse(ok=False)

    # 2. Flip the flag only after the inserts succeeded.
    try:
        state_set(_state_dir(), "onboarding.complete", "true")
    except Exception as exc:  # pragma: no cover — disk / permission errors
        logger.exception("failed to write onboarding_complete flag")
        return CompleteResponse(ok=False)
    logger.info(
        "onboarding marked complete",
        extra={
            "admin_count": len(admins),
            "action_items_inserted": inserted,
        },
    )
    return CompleteResponse(ok=True)


@router.post("/restart", response_model=RestartResponse)
async def restart_onboarding(_payload: RestartRequest) -> RestartResponse:
    """Clear the ``onboarding_complete`` flag.

    Called by the dashboard "Restart onboarding" button. The saved
    bot token and super-admin list are intentionally left in place
    so the wizard's resume logic (Step 1 view mode, prefilled admin
    rows) picks them up again — a deployer can re-confirm a setup
    without re-typing the tgids.

    Clears both the canonical key (``onboarding.complete``) and
    the legacy v0 key (``telegram.onboarding_complete``) so a
    deployer's previous setting doesn't accidentally keep them
    out of the wizard. The legacy key is read-only on the
    status path; ``/restart`` is the one place that writes a
    delete for it too.
    """
    from magi.agent.db.settings import state_delete

    try:
        state_delete(_state_dir(), "onboarding.complete")
        state_delete(_state_dir(), "telegram.onboarding_complete")
    except Exception as exc:  # pragma: no cover — disk / permission errors
        logger.exception("failed to clear onboarding_complete flag")
        return RestartResponse(ok=False)
    logger.info("onboarding marked incomplete (restart)")
    return RestartResponse(ok=True)


@router.post("/verify-bot", response_model=VerifyBotResponse)
async def verify_bot(payload: VerifyBotRequest) -> VerifyBotResponse:
    """Verify a Telegram bot token via the official ``getMe`` call.

    Never stores anything. Returns ``{ok: true, username}`` on a real
    bot, or ``{ok: false, error}`` for any failure (network, HTTP, or
    Telegram's own ``description`` field).
    """
    token = payload.token.strip()
    if not token:
        return VerifyBotResponse(ok=False, error="Token is empty")

    url = f"https://api.telegram.org/bot{token}/getMe"
    try:
        async with httpx.AsyncClient(timeout=_TELEGRAM_TIMEOUT_SECONDS) as client:
            resp = await client.get(url)
    except httpx.TimeoutException:
        return VerifyBotResponse(ok=False, error="Telegram timed out")
    except httpx.RequestError as exc:
        return VerifyBotResponse(ok=False, error=f"Network error: {exc}")

    if resp.status_code != 200:
        return VerifyBotResponse(
            ok=False,
            error=f"Telegram returned HTTP {resp.status_code}",
        )

    data = resp.json()
    if not data.get("ok"):
        description = data.get("description", "Unknown error from Telegram")
        return VerifyBotResponse(ok=False, error=description)

    result = data.get("result") or {}
    username = result.get("username")
    if not username:
        return VerifyBotResponse(
            ok=False,
            error="Telegram response missing bot username",
        )

    return VerifyBotResponse(ok=True, username=username)


@router.post("/save-bot", response_model=SaveBotResponse)
async def save_bot(payload: SaveBotRequest) -> SaveBotResponse:
    """Persist the verified bot token + username into the settings table.

    The frontend guarantees the token passed ``verify-bot`` immediately
    before this call. Re-verifying here would cost an extra Telegram
    round-trip for no gain; the only way a stale token lands in the
    DB is if the deployer's network is hijacked between clicks.
    """
    from magi.agent.db.settings import state_set

    state_dir = _state_dir()
    try:
        state_set(state_dir, "telegram.bot_token", payload.token)
        state_set(state_dir, "telegram.bot_username", payload.username)
    except Exception as exc:  # pragma: no cover — disk / permission errors
        logger.exception("failed to write settings")
        return SaveBotResponse(ok=False, error=str(exc))

    logger.info(
        "bot token saved",
        extra={"username": payload.username, "state_dir": state_dir},
    )
    return SaveBotResponse(ok=True)


@router.post("/verify-admin", response_model=VerifyAdminResponse)
async def verify_admin(payload: VerifyAdminRequest) -> VerifyAdminResponse:
    """Backward-compat alias for ``send-admin-code`` — older frontend
    versions call ``/verify-admin`` to get the bot to send the user a
    test message. The new code-based flow uses ``/send-admin-code``
    and ``/verify-admin-code`` instead.
    """
    return await _send_admin_code_inner(
        SendAdminCodeRequest(delivery_address=payload.delivery_address)
    )


@router.post("/send-admin-code", response_model=SendAdminCodeResponse)
async def send_admin_code(payload: SendAdminCodeRequest) -> SendAdminCodeResponse:
    """Generate a one-time 6-digit code, store it in ``settings``, and
    send it to the tgid via the saved bot. The user reads the
    code in Telegram, types it back into the wizard, and
    ``/verify-admin-code`` confirms it matches.

    Requires ``telegram.bot_token`` to already be saved (step 2). The
    bot must have been started by the user (``/start`` in TG) —
    otherwise Telegram's privacy mode may reject the message.
    """
    return await _send_admin_code_inner(payload)


# Code TTL: 5 minutes. Long enough to copy the code from TG into the
# browser; short enough that a leaked code is not a long-lived
# attack surface.
_CODE_TTL_SECONDS = 300

# Resend cooldown: a user can hit "Send code" again after this many
# seconds, even if the previous code is still live. Prevents an impatient
# user (or a stuck-network retry loop) from spamming TG. 60s is short
# enough to feel responsive on a fluke, long enough to rate-limit an
# accidental double-click or three.
_RESEND_COOLDOWN_SECONDS = 60


def _generate_code() -> str:
    """Cryptographically-random 6-digit code, zero-padded."""
    import secrets

    return f"{secrets.randbelow(1_000_000):06d}"


async def _send_admin_code_inner(payload: SendAdminCodeRequest) -> SendAdminCodeResponse:
    """Shared body for the public endpoints and the back-compat alias."""
    from datetime import datetime, timezone
    from magi.agent.db.settings import state_get, state_set

    bot_token = state_get(_state_dir(), "telegram.bot_token")
    if not bot_token:
        return SendAdminCodeResponse(
            ok=False,
            error="Bot token not saved yet — finish step 2 first.",
        )

    tgid_raw = payload.delivery_address.strip()
    if not tgid_raw.lstrip("-").isdigit():
        return SendAdminCodeResponse(ok=False, error="tgid must be numeric")
    tgid = tgid_raw  # keep as string for settings key consistency

    # Resend cooldown — a stuck-network retry or impatient user must
    # wait before we spam the chat with another code. We check the
    # LAST SENT timestamp stored in settings (separate from the code's
    # own expiry so the cooldown applies even if the previous code is
    # already expired).
    from magi.agent.db.settings import state_get
    previous = state_get(_state_dir(), f"telegram.verify_code.{tgid}")
    if previous:
        try:
            prev_data = json.loads(previous)
            prev_sent_at = float(prev_data.get("last_sent_at", 0))
        except (TypeError, ValueError, json.JSONDecodeError):
            prev_sent_at = 0
        if prev_sent_at:
            elapsed = datetime.now(timezone.utc).timestamp() - prev_sent_at
            if elapsed < _RESEND_COOLDOWN_SECONDS:
                remaining = int(_RESEND_COOLDOWN_SECONDS - elapsed)
                # How much life the old code still has (may already be
                # 0 if the previous send was close to its expiry).
                prev_expires = float(prev_data.get("expires_at", 0))
                prev_remaining = max(
                    0, int(prev_expires - datetime.now(timezone.utc).timestamp())
                )
                return SendAdminCodeResponse(
                    ok=False,
                    error=(
                        f"Wait {remaining}s before requesting a new code."
                        + (
                            f" Your previous code is still valid for another "
                            f"{prev_remaining}s if you have it."
                            if prev_remaining > 0
                            else " Your previous code already expired."
                        )
                    ),
                )

    code = _generate_code()
    issued_at = datetime.now(timezone.utc)
    expires_at = issued_at.timestamp() + _CODE_TTL_SECONDS

    # Persist BEFORE we send — if Telegram fails, the user can retry
    # with the same code still in settings, no surprise active codes.
    state_set(
        _state_dir(),
        f"telegram.verify_code.{tgid}",
        json.dumps(
            {
                "code": code,
                "issued_at": issued_at.replace(microsecond=0).isoformat(),
                "expires_at": expires_at,
                "last_sent_at": issued_at.timestamp(),
            }
        ),
    )

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=_TELEGRAM_TIMEOUT_SECONDS) as client:
            resp = await client.post(
                url,
                json={
                    "tgid": int(tgid),
                    "text": (
                        f"Your MAGI setup code is: <code>{code}</code>\n\n"
                        f"Enter this code in the MAGI admin wizard to "
                        f"verify your tgid. The code expires in "
                        f"{_CODE_TTL_SECONDS // 60} minutes."
                    ),
                },
            )
    except httpx.TimeoutException:
        return SendAdminCodeResponse(ok=False, error="Telegram timed out")
    except httpx.RequestError as exc:
        return SendAdminCodeResponse(ok=False, error=f"Network error: {exc}")

    if resp.status_code != 200:
        return SendAdminCodeResponse(
            ok=False,
            error=f"Telegram returned HTTP {resp.status_code}",
        )

    data = resp.json()
    if not data.get("ok"):
        # If the send failed, the code is now stale. Remove it so a
        # re-send issues a fresh one.
        from magi.agent.db.settings import state_delete

        state_delete(_state_dir(), f"telegram.verify_code.{tgid}")
        description = data.get("description", "Unknown error from Telegram")
        return SendAdminCodeResponse(ok=False, error=description)

    logger.info(
        "admin verification code sent",
        extra={"tgid": tgid, "ttl_seconds": _CODE_TTL_SECONDS},
    )
    return SendAdminCodeResponse(ok=True, expires_in=_CODE_TTL_SECONDS)


@router.post("/verify-admin-code", response_model=VerifyAdminCodeResponse)
async def verify_admin_code(payload: VerifyAdminCodeRequest) -> VerifyAdminCodeResponse:
    """Check the code the user typed against the one we sent to the
    tgid. On success:

    1. **Expiry check** — code must be within the 5-minute TTL.
    2. **One-shot** — burn the code on any attempt (success, mismatch,
       or expiry) so a wrong-guess attacker can't grind through the
       6^6 space against a still-valid code.
    3. **Don't persist yet** — the user's tgid is
       recorded only after they finish the wizard via
       ``save_admin`` (the Employee row + ``role='admin'``
       is the single source of truth). Verify just proves
       ownership; the operator still has to confirm the
       final admin list.
    4. Fetch a display name via ``getChat`` for the frontend.
    """
    from datetime import datetime, timezone
    from magi.agent.db.settings import state_get

    tgid = payload.delivery_address.strip()
    code = payload.code.strip()
    if not code.isdigit() or len(code) != 6:
        return VerifyAdminCodeResponse(ok=False, error="Code must be 6 digits")

    raw = state_get(_state_dir(), f"telegram.verify_code.{tgid}")
    if not raw:
        return VerifyAdminCodeResponse(
            ok=False,
            error="No code sent to this tgid — request a new one.",
        )

    try:
        payload_data = json.loads(raw)
    except (ValueError, TypeError):
        logger.warning("stored verify code is not valid JSON for delivery_address=%s", tgid)
        return VerifyAdminCodeResponse(ok=False, error="Stored code is corrupt; request a new one.")

    stored = str(payload_data.get("code", ""))

    # Expiry check first, so a stale code is reported as "expired"
    # (not "does not match" — friendlier when the user just took
    # too long). 5-minute TTL matches the in-TG message the bot
    # already sends.
    try:
        expires_at = float(payload_data.get("expires_at", 0))
    except (TypeError, ValueError):
        expires_at = 0
    now_ts = datetime.now(timezone.utc).timestamp()

    from magi.agent.db.settings import state_delete
    if not expires_at or now_ts >= expires_at:
        state_delete(_state_dir(), f"telegram.verify_code.{tgid}")
        return VerifyAdminCodeResponse(
            ok=False,
            error="Code expired — request a new one.",
        )

    # Burn on any path that gets past expiry (mismatch, success,
    # anything) so the code can't be re-tried by an attacker.
    state_delete(_state_dir(), f"telegram.verify_code.{tgid}")

    if stored != code:
        return VerifyAdminCodeResponse(ok=False, error="Code does not match")

    # The code match is the proof-of-ownership; we don't persist
    # the tgid here. The wizard's ``save_admin`` step (the
    # final "Save" button) is what writes admin rows to the
    # ``employees`` table — that path is the single source of
    # truth for "who's an admin". Persisting at this point
    # would create Employee rows that the operator might
    # later remove via save_admin's diff step, doubling the
    # work for no gain.

    display_name = await _fetch_display_name(tgid)
    logger.info(
        "admin tgid verified via code",
        extra={"tgid": tgid, "display_name": display_name},
    )
    return VerifyAdminCodeResponse(ok=True, display_name=display_name)


async def _fetch_display_name(tgid: str) -> str | None:
    """Call Telegram ``getChat`` so the UI can show "Verified — Alice"
    instead of just a bare tgid. Failures degrade silently to None."""
    from magi.agent.db.settings import state_get

    bot_token = state_get(_state_dir(), "telegram.bot_token")
    if not bot_token:
        return None
    url = f"https://api.telegram.org/bot{bot_token}/getChat"
    try:
        async with httpx.AsyncClient(timeout=_TELEGRAM_TIMEOUT_SECONDS) as client:
            r = await client.post(url, json={"tgid": int(tgid)})
    except (httpx.TimeoutException, httpx.RequestError, ValueError):
        return None
    if r.status_code != 200:
        return None
    data = r.json()
    if not data.get("ok"):
        return None
    chat = data.get("result") or {}
    return chat.get("first_name") or chat.get("title") or chat.get("username")


def _now_iso() -> str:
    """UTC ISO timestamp without microseconds — the settings table is
    text-only and we don't need sub-second precision for a 5-min TTL."""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@router.post("/save-admin", response_model=SaveAdminResponse)
async def save_admin(payload: SaveAdminRequest) -> SaveAdminResponse:
    """Replace the super-admin set with the verified list.

    Each entry becomes an :class:`Employee` row with
    ``role='admin'`` and ``telegram_id=<tgid>``, living
    under no department (the "未指定部门" scope). Display
    name is resolved via Telegram ``getChat`` so the
    dashboard can show "Alice" instead of "12345" without a
    second round-trip per row.

    Side effects on each call:
      - Any prior ``Employee`` with ``role='admin'`` whose
        ``telegram_id`` isn't in the new list is **deleted**
        (these rows were created by onboarding too; they
        have no business data so dropping is safe).
      - Any prior ``Employee`` with ``telegram_id`` in the
        new list gets its ``role`` flipped to ``admin`` even
        if it was previously a regular employee (this
        handles the rare case where someone was first added
        to the company, then promoted to admin).

    No settings key is written; the Employee table is the
    single source of truth for "who's an admin". The auth
    gate (``_is_admin_or_assigned_tgid`` in ``departments.py``) reads
    exclusively from this table.
    """
    from magi.agent.db import Employee, open_session

    state_dir = _state_dir()
    cleaned = sorted({c.strip() for c in payload.tgids if c.strip()})
    if not cleaned:
        return SaveAdminResponse(ok=False, error="At least one tgid required")
    # Each tgid must be a TG-compatible integer (possibly
    # negative for group chats).
    parsed_ids: list[int] = []
    for c in cleaned:
        try:
            parsed_ids.append(int(c))
        except ValueError:
            return SaveAdminResponse(
                ok=False,
                error=f"tgid must be numeric, got {c!r}",
            )

    # Display name resolution runs in parallel for all ids —
    # they're independent HTTPS calls, no point serialising.
    display_names: dict[int, str | None] = {}
    if parsed_ids:
        results = await asyncio.gather(
            *(_fetch_display_name(c) for c in parsed_ids),
            return_exceptions=True,
        )
        for cid, name in zip(parsed_ids, results):
            if isinstance(name, BaseException):
                # getChat failed (timeout, 4xx, etc.). The admin
                # row is still created — we just fall back to
                # the tgid as the display. The row's name
                # field holds the human-readable label (see
                # below).
                display_names[cid] = None
            else:
                display_names[cid] = name

    try:
        with open_session() as session:
            # 1) Existing admin rows not in the new list → delete
            #    (these are onboarding-created shells; no
            #    business data so dropping is safe).
            existing_admins = session.scalars(
                select(Employee).where(Employee.role == "admin")
            ).all()
            new_id_set = set(parsed_ids)
            for old in existing_admins:
                if old.telegram_id is None or old.telegram_id not in new_id_set:
                    session.delete(old)

            # 2) Each new tgid → ensure an Employee row
            #    exists with role=admin, telegram_id=<id>,
            #    department_id=null. Promote existing regular
            #    employees in the rare case the tgid was
            #    already bound.
            for cid in parsed_ids:
                emp = session.scalar(
                    select(Employee).where(Employee.telegram_id == cid)
                )
                if emp is None:
                    emp = Employee(
                        name=display_names[cid] or f"Admin {cid}",
                        display_name=display_names[cid],
                        department_id=None,
                        role="admin",
                        telegram_id=cid,
                    )
                    session.add(emp)
                else:
                    emp.role = "admin"
                    if display_names[cid]:
                        emp.name = display_names[cid]
                        if not emp.display_name:
                            emp.display_name = display_names[cid]
            session.commit()
    except Exception as exc:
        logger.exception("failed to write admin employees")
        return SaveAdminResponse(ok=False, error=str(exc))

    logger.info("admins saved", extra={"count": len(cleaned)})
    return SaveAdminResponse(ok=True, count=len(cleaned))