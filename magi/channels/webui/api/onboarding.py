"""Onboarding API — three-step flow for first-time setup.

    1. Bot token (verify + save)
       ``POST /api/onboarding/verify-bot { token }``
           Calls Telegram's ``getMe``. Returns ``{ok, username}`` or
           ``{ok: false, error}``. **Does not store**.
       ``POST /api/onboarding/save-bot { token, username }``
           Writes the bot token and username into the ``settings`` table.

    2. (implicit / no API) The "Saved" page just displays the
       persisted token + username; the user clicks Next to step 3.

    3. Super admin chat_ids (verify + save)
       ``POST /api/onboarding/verify-admin { chat_id }``
           Sends a connectivity test message to ``chat_id`` via the
           saved bot. Returns ``{ok, display_name}`` or ``{ok: false,
           error}``. **Does not store**.
       ``POST /api/onboarding/save-admin { chat_ids: list[str] }``
           Writes the verified chat_id list (JSON-encoded) into
           ``telegram.super_admins`` in the ``settings`` table.

All four endpoints are read-only or write-only against the ``settings``
table, so they live alongside the webui channel rather than in a future
``magi/adam/`` package.
"""

from __future__ import annotations

import json
import logging
import os

import httpx
from fastapi import APIRouter
from pydantic import BaseModel, Field

logger = logging.getLogger("magi.api.onboarding")

router = APIRouter(tags=["onboarding"])

# 5s is generous for a single Telegram call.
_TELEGRAM_TIMEOUT_SECONDS = 5.0


def _state_dir() -> str:
    """Read MAGI_STATE_DIR each call — keeps state_dir testable + env-friendly."""
    return os.environ.get("MAGI_STATE_DIR", "/workspace/memories")


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
    chat_id: str = Field(min_length=1, max_length=64)


class VerifyAdminResponse(BaseModel):
    ok: bool
    display_name: str | None = None
    error: str | None = None


class SendAdminCodeRequest(BaseModel):
    chat_id: str = Field(min_length=1, max_length=64)


class SendAdminCodeResponse(BaseModel):
    ok: bool
    expires_in: int = 0  # seconds until the code expires
    error: str | None = None


class VerifyAdminCodeRequest(BaseModel):
    chat_id: str = Field(min_length=1, max_length=64)
    code: str = Field(min_length=6, max_length=6)


class VerifyAdminCodeResponse(BaseModel):
    ok: bool
    display_name: str | None = None
    error: str | None = None


class SaveAdminRequest(BaseModel):
    chat_ids: list[str] = Field(min_length=1)


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
    # deliberately decoupled from bot_saved + super_admins_count so a
    # user who saved a bot but abandoned step 3 can still get back
    # into the wizard (and so a deployer can "Restart onboarding"
    # without nuking the saved data).
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
    from magi.runtime.state.settings import state_get

    state_dir = _state_dir()
    bot_username = state_get(state_dir, "telegram.bot_username")
    raw_admins = state_get(state_dir, "telegram.super_admins")
    admins: list[str] = []
    if raw_admins:
        try:
            parsed = json.loads(raw_admins)
            if isinstance(parsed, list):
                admins = [str(x) for x in parsed]
        except (ValueError, TypeError):
            logger.warning(
                "telegram.super_admins in settings is not valid JSON; treating as empty"
            )

    # "True" / "true" / "1" all count. Anything else (including
    # missing) is False. Kept as a plain text flag — the only
    # writer is /complete, which writes the literal "true".
    complete_raw = state_get(state_dir, "telegram.onboarding_complete")
    onboarding_complete = str(complete_raw).strip().lower() in ("true", "1")

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
    """
    from magi.runtime.state.settings import state_set

    try:
        state_set(_state_dir(), "telegram.onboarding_complete", "true")
    except Exception as exc:  # pragma: no cover — disk / permission errors
        logger.exception("failed to write onboarding_complete flag")
        return CompleteResponse(ok=False)
    logger.info("onboarding marked complete")
    return CompleteResponse(ok=True)


@router.post("/restart", response_model=RestartResponse)
async def restart_onboarding(_payload: RestartRequest) -> RestartResponse:
    """Clear the ``onboarding_complete`` flag.

    Called by the dashboard "Restart onboarding" button. The saved
    bot token and super-admin list are intentionally left in place
    so the wizard's resume logic (Step 1 view mode, prefilled admin
    rows) picks them up again — a deployer can re-confirm a setup
    without re-typing the chat_ids.
    """
    from magi.runtime.state.settings import state_delete

    try:
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
    from magi.runtime.state.settings import state_set

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
        SendAdminCodeRequest(chat_id=payload.chat_id)
    )


@router.post("/send-admin-code", response_model=SendAdminCodeResponse)
async def send_admin_code(payload: SendAdminCodeRequest) -> SendAdminCodeResponse:
    """Generate a one-time 6-digit code, store it in ``settings``, and
    send it to the chat_id via the saved bot. The user reads the
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
    from magi.runtime.state.settings import state_get, state_set

    bot_token = state_get(_state_dir(), "telegram.bot_token")
    if not bot_token:
        return SendAdminCodeResponse(
            ok=False,
            error="Bot token not saved yet — finish step 2 first.",
        )

    chat_id_raw = payload.chat_id.strip()
    if not chat_id_raw.lstrip("-").isdigit():
        return SendAdminCodeResponse(ok=False, error="chat_id must be numeric")
    chat_id = chat_id_raw  # keep as string for settings key consistency

    # Resend cooldown — a stuck-network retry or impatient user must
    # wait before we spam the chat with another code. We check the
    # LAST SENT timestamp stored in settings (separate from the code's
    # own expiry so the cooldown applies even if the previous code is
    # already expired).
    from magi.runtime.state.settings import state_get
    previous = state_get(_state_dir(), f"telegram.verify_code.{chat_id}")
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
        f"telegram.verify_code.{chat_id}",
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
                    "chat_id": int(chat_id),
                    "text": (
                        f"Your MAGI setup code is: <code>{code}</code>\n\n"
                        f"Enter this code in the MAGI admin wizard to "
                        f"verify your chat_id. The code expires in "
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
        from magi.runtime.state.settings import state_delete

        state_delete(_state_dir(), f"telegram.verify_code.{chat_id}")
        description = data.get("description", "Unknown error from Telegram")
        return SendAdminCodeResponse(ok=False, error=description)

    logger.info(
        "admin verification code sent",
        extra={"chat_id": chat_id, "ttl_seconds": _CODE_TTL_SECONDS},
    )
    return SendAdminCodeResponse(ok=True, expires_in=_CODE_TTL_SECONDS)


@router.post("/verify-admin-code", response_model=VerifyAdminCodeResponse)
async def verify_admin_code(payload: VerifyAdminCodeRequest) -> VerifyAdminCodeResponse:
    """Check the code the user typed against the one we sent to the
    chat_id. On success:

    1. **Expiry check** — code must be within the 5-minute TTL.
    2. **One-shot** — burn the code on any attempt (success, mismatch,
       or expiry) so a wrong-guess attacker can't grind through the
       6^6 space against a still-valid code.
    3. **Append the chat_id to ``telegram.super_admins``** on success.
    4. Fetch a display name via ``getChat`` for the frontend.
    """
    from datetime import datetime, timezone
    from magi.runtime.state.settings import state_get

    chat_id = payload.chat_id.strip()
    code = payload.code.strip()
    if not code.isdigit() or len(code) != 6:
        return VerifyAdminCodeResponse(ok=False, error="Code must be 6 digits")

    raw = state_get(_state_dir(), f"telegram.verify_code.{chat_id}")
    if not raw:
        return VerifyAdminCodeResponse(
            ok=False,
            error="No code sent to this chat_id — request a new one.",
        )

    try:
        payload_data = json.loads(raw)
    except (ValueError, TypeError):
        logger.warning("stored verify code is not valid JSON for chat_id=%s", chat_id)
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

    from magi.runtime.state.settings import state_delete
    if not expires_at or now_ts >= expires_at:
        state_delete(_state_dir(), f"telegram.verify_code.{chat_id}")
        return VerifyAdminCodeResponse(
            ok=False,
            error="Code expired — request a new one.",
        )

    # Burn on any path that gets past expiry (mismatch, success,
    # anything) so the code can't be re-tried by an attacker.
    state_delete(_state_dir(), f"telegram.verify_code.{chat_id}")

    if stored != code:
        return VerifyAdminCodeResponse(ok=False, error="Code does not match")

    # Commit this chat_id to the super-admin list immediately — the
    # user has proven they own the chat, so there's no point making
    # them click "Finish" before the result sticks. Idempotent.
    _append_super_admin(chat_id)

    # Best-effort: also fetch a display name for the frontend. Don't
    # fail the verify call if getChat errors out — the code match is
    # the source of truth.
    display_name = await _fetch_display_name(chat_id)
    logger.info(
        "admin chat_id verified via code",
        extra={"chat_id": chat_id, "display_name": display_name},
    )
    return VerifyAdminCodeResponse(ok=True, display_name=display_name)


def _append_super_admin(chat_id: str) -> None:
    """Add ``chat_id`` to ``telegram.super_admins`` (deduped + sorted).

    No-op if it's already in the list. The list is stored as a JSON
    array so the bot can read it cheaply on every inbound message.
    """
    from magi.runtime.state.settings import state_get

    state_dir = _state_dir()
    raw = state_get(state_dir, "telegram.super_admins")
    admins: set[str] = set()
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                admins = {str(x) for x in parsed}
        except (ValueError, TypeError):
            logger.warning(
                "telegram.super_admins is not valid JSON; resetting"
            )
    admins.add(chat_id)
    from magi.runtime.state.settings import state_set

    state_set(state_dir, "telegram.super_admins", json.dumps(sorted(admins)))
    logger.info(
        "super_admins list updated",
        extra={"added": chat_id, "count": len(admins)},
    )


async def _fetch_display_name(chat_id: str) -> str | None:
    """Call Telegram ``getChat`` so the UI can show "Verified — Alice"
    instead of just a bare chat_id. Failures degrade silently to None."""
    from magi.runtime.state.settings import state_get

    bot_token = state_get(_state_dir(), "telegram.bot_token")
    if not bot_token:
        return None
    url = f"https://api.telegram.org/bot{bot_token}/getChat"
    try:
        async with httpx.AsyncClient(timeout=_TELEGRAM_TIMEOUT_SECONDS) as client:
            r = await client.post(url, json={"chat_id": int(chat_id)})
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
    """Persist the verified super-admin chat_id list.

    JSON-encoded into ``settings.telegram.super_admins``. The frontend
    guarantees each id was verified via ``/verify-admin`` immediately
    before this call.
    """
    from magi.runtime.state.settings import state_set

    state_dir = _state_dir()
    # Trim + dedupe + drop empties.
    cleaned = sorted({c.strip() for c in payload.chat_ids if c.strip()})
    if not cleaned:
        return SaveAdminResponse(ok=False, error="At least one chat_id required")

    try:
        state_set(state_dir, "telegram.super_admins", json.dumps(cleaned))
    except Exception as exc:
        logger.exception("failed to write super_admins")
        return SaveAdminResponse(ok=False, error=str(exc))

    logger.info("super admins saved", extra={"count": len(cleaned)})
    return SaveAdminResponse(ok=True, count=len(cleaned))