"""Onboarding API — three-step flow for first-time setup.

    1. Bot token (verify + save)
       ``POST /api/onboarding/verify-bot { token }``
           Calls Telegram's ``getMe``. Returns ``{ok, username}`` or
           ``{ok: false, error}``. **Does not store**.
       ``POST /api/onboarding/save-bot { token, username }``
           Writes the bot token and username into the ``settings`` table.

    2. (implicit / no API) The "Saved" page just displays the
       persisted token + username; the user clicks Next to step 3.

    3. Super admin delivery addresses (verify + save)
       ``POST /api/onboarding/verify-admin { tgid }``
           Sends a connectivity test message to the bound TG chat via
           the saved bot. Returns ``{ok, display_name}`` or
           ``{ok: false, error}``. **Does not store**.
       ``POST /api/onboarding/save-admin { tgids: list[str] }``
           Upserts a ``Contact`` row per delivery address with
           ``role='admin'`` and its TG delivery address,
           with no team. Display names are resolved via Telegram
           ``getChat``. Idempotent.

All four endpoints are read-only or write-only against the ``settings``
table, so they live alongside the webui channel rather than in a future
``magi/adam/`` package.
"""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter
from pydantic import BaseModel, Field

from magi.bus.library.local.actionItemBook import SOURCE_PROACTIVE
from magi.bus import Bus
from magi.channels.api.dependencies import BusDep, WorkersDep
from magi.channels.telegram import bot as tg_bot
from magi.channels import Channel
from magi.channels.api import control_store

logger = logging.getLogger("magi.api.onboarding")

router = APIRouter(tags=["onboarding"])




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
    # ``login_methods`` summarises which login options
    # the wizard's owner (the first admin) currently has.
    # "" means "no admin row yet" — pre-step-2 the wizard
    # is still collecting the operator's identity.
    login_methods: list[str] = []
    # ``mode`` is the wizard's chosen branch — empty until
    # step 1 commits to "webui_only" or "with_tg". The
    # /complete endpoint accepts either branch as long as
    # the matching credentials are saved.
    mode: str | None = None


class CompleteResponse(BaseModel):
    ok: bool
    error: str | None = None


class RestartResponse(BaseModel):
    ok: bool


class SetAdminPasswordRequest(BaseModel):
    """WebUI-only onboarding step 2 input.

    The wizard collects a name + password for the
    operator's first admin row. The endpoint upserts the
    :class:`Contact` row with ``admin=True`` and writes a
    hashed :class:`AuthCredential` (kind=password) so the
    operator can sign in without a Telegram binding.
    """

    name: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=8, max_length=256)


class SetAdminPasswordResponse(BaseModel):
    ok: bool
    error: str | None = None
    admin_uid: int | None = None


# -- endpoints ---------------------------------------------------------


@router.get("/status", response_model=OnboardingStatus)
async def get_status(bus: BusDep) -> OnboardingStatus:
    """Read-only summary of the persisted onboarding state.

    The frontend calls this on mount to decide whether to start the
    wizard at step 1 (nothing saved) or skip directly to step 2 / 3
    (bot already saved, optionally with super admins).

    ``onboarding_complete`` is the only field the boot routing
    trusts: it's a strict bool written by ``/complete`` (dashboard
    "OK, got it") and cleared by ``/restart``. Everything else is
    informational / for the wizard's own resume logic.
    """
    if control_store.enabled():
        root = bus.magis_book.get_root() if bus.magis_book else None
        root_id = root.id if root else None
        admins = (
            [str(a.uid) for a in bus.magis_admins_book.list_for_magis(magis_id=root_id)]
            if bus.magis_admins_book is not None and root_id is not None
            else []
        )
        username = control_store.get(bus, "telegram.bot_username")
        return OnboardingStatus(
            bot_saved=bool(username), bot_username=username,
            super_admins_count=len(admins), super_admins=admins,
            onboarding_complete=(control_store.get(bus, "onboarding.complete") or "").lower() in {"true", "1"},
            login_methods=["tg_code"] if admins else [],
            mode="with_tg" if admins else None,
        )

    bot_username = bus.settings_book.get(key="telegram.bot_username")

    # Super admins live in the contacts table (unified with
    # the rest of the org directory) — that's the single source
    # of truth. The wizard resumes by reading from there so
    # the "you already added N admins" message reflects the
    # canonical state. There's no settings-key fallback by
    # design: a state file pre-C1.x may have a stale
    # ``telegram.super_admins`` key, but the operator can
    # always re-save the admin list to clean that up.
    admins: list[str] = []
    login_methods: list[str] = []
    chosen_mode: str | None = None
    try:
        admin_rows = bus.contacts_book.list_admins()
        for admin in admin_rows:
            if admin.telegram_id is not None:
                admins.append(str(admin.telegram_id))
        # ``login_methods`` summarises the wizard's
        # owner (the first admin row). The wizard
        # only ever sets up one admin's credentials
        # during onboarding, so the first row is the
        # source of truth.
        if admin_rows:
            first = admin_rows[0]
            has_password = bool(bus.auth_credentials_book and bus.auth_credentials_book.find(uid=first.id, kind="password"))
            if has_password:
                login_methods.append("password")
            if first.telegram_id is not None:
                login_methods.append("tg_code")
            chosen_mode = "with_tg" if first.telegram_id else (
                "webui_only" if has_password else None
            )
    except Exception:
        # If the table is unreachable (very early boot) the
        # wizard still loads; admins stays empty until the
        # operator re-saves.
        logger.exception("failed to read admin contacts")

    # "True" / "true" / "1" all count. Anything else (including
    # missing) is False. Kept as a plain text flag — the only
    # writer is /complete, which writes the literal "true".
    #
    # The key is ``onboarding.complete`` (not
    # ``telegram.onboarding_complete``) because "is the
    # operator's first-time setup done?" is a system-level
    # state, not a channel-level one. The channel-level keys
    # (``telegram.bot_token``, ``telegram.bot_username``,
    # ``telegram.verify_code.<chat-id>``) legitimately carry
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
    complete_raw = bus.settings_book.get(key="onboarding.complete")
    if complete_raw is None:
        # Pre-rename deployments still have the older
        # ``telegram.onboarding_complete`` key. Read it once,
        # migrate forward lazily (don't write here — the
        # wizard's completion will write the new key).
        old_raw = bus.settings_book.get(key="telegram.onboarding_complete")
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
        login_methods=login_methods,
        mode=chosen_mode,
    )


@router.post("/set-admin-password", response_model=SetAdminPasswordResponse)
async def set_admin_password_onboarding(
    payload: SetAdminPasswordRequest,
    bus: BusDep,
) -> SetAdminPasswordResponse:
    """WebUI-only onboarding step 2: create the first admin.

    The TG wizard's step 2 collects TG chat ids via
    :func:`save_admin`; this endpoint is the parallel
    flow for operators who picked "WebUI only" in step 1.
    It upserts a ``Contact`` row with ``admin=True`` and
    ``role='assigned'`` (the operator is the person
    being served, which is the single-MAGI default) and
    writes a hashed :class:`AuthCredential`. There is no
    telegram_id — the row is a WebUI-only admin.

    If a previous admin row exists (an operator who
    abandoned the wizard and re-entered the password
    step), the first admin row is reused so the onboarding
    flow doesn't strand the original row's chat history.
    """
    if control_store.enabled():
        # Control-plane side is out of scope for this PR.
        return SetAdminPasswordResponse(
            ok=False,
            error="password login is not supported by the control plane",
        )

    name = payload.name.strip()
    if not name:
        return SetAdminPasswordResponse(ok=False, error="name is required")

    from magi.channels.api import password_utils

    try:
        new_hash = password_utils.hash_password(payload.password)
    except ValueError as exc:
        return SetAdminPasswordResponse(ok=False, error=str(exc))

    # Upsert the first admin Contact row (create on first call, rename on
    # subsequent calls so chat history survives a re-entered wizard).
    admin_uid = bus.contacts_book.upsert_first_admin(name=name)
    # Upsert the password credential.
    if bus.auth_credentials_book is not None:
        bus.auth_credentials_book.add(uid=admin_uid, kind="password", secret_hash=new_hash)
    else:
        bus.settings_book.set(key=f"auth.password.{admin_uid}.hash", value=new_hash)

    logger.info(
        "onboarding: admin password set",
        extra={"uid": admin_uid, "name": name},
    )
    return SetAdminPasswordResponse(ok=True, admin_uid=admin_uid)


@router.post("/complete", response_model=CompleteResponse)
async def complete_onboarding(bus: BusDep) -> CompleteResponse:
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
    if control_store.enabled():
        control_store.set(bus, "onboarding.complete", "true")
        return CompleteResponse(ok=True)

    # 1. Stamp one credentials nudge per current admin via
    #    bus.action_items_book.  Idempotent — re-running is a
    #    no-op for any admin that already has an open row.
    _NUDGE_TITLE = "设置你的 LLM provider 和 API key"
    _NUDGE_DESC = (
        "切到「Contacts」,找到自己的档案,"
        "把 Provider 和 API Key 填上。"
    )
    _NUDGE_URL = "/dashboard?tab=organization"
    try:
        admins = bus.contacts_book.list_admins()
        inserted = 0
        for admin in admins:
            existing_open = [
                row for row in bus.action_items_book.list_actions(
                    owner_uid=admin.id,
                    include_completed=False,
                    source=SOURCE_PROACTIVE,
                )
                if row.title == _NUDGE_TITLE
            ]
            if existing_open:
                continue
            existing_done = [
                row for row in bus.action_items_book.list_actions(
                    owner_uid=admin.id,
                    include_completed=True,
                    source=SOURCE_PROACTIVE,
                )
                if row.title == _NUDGE_TITLE and row.completed_at is not None
            ]
            if existing_done:
                continue
            bus.action_items_book.add(
                uid=admin.id,
                title=_NUDGE_TITLE,
                description=_NUDGE_DESC,
                target_url=_NUDGE_URL,
                source=SOURCE_PROACTIVE,
            )
            inserted += 1
    except Exception:  # pragma: no cover — DB failure
        logger.exception("complete: action-item insert failed")
        return CompleteResponse(ok=False)

    # 1.5. Branch-aware credential check. A WebUI-only
    #      wizard must have at least one admin with a
    #      password credential before ``/complete`` is
    #      allowed to flip the flag — otherwise the
    #      operator couldn't sign in. The TG branch
    #      relies on the existing admin-row check above.
    if admins:
        has_password = any(
            bus.auth_credentials_book and bus.auth_credentials_book.find(uid=admin.id, kind="password")
            for admin in admins
        )
        # ``has_tg`` = any admin has a telegram_id.
        has_tg = any(admin.telegram_id for admin in admins)
        if not has_tg and not has_password:
            return CompleteResponse(
                ok=False,
                error=(
                    "no login method configured: pick WebUI-only "
                    "(set a password) or with-TG (bind a chat id) "
                    "before completing onboarding"
                ),
            )

    # 2. Flip the flag only after the inserts succeeded.
    try:
        bus.settings_book.set(key="onboarding.complete", value="true")
    except Exception:  # pragma: no cover — disk / permission errors
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
async def restart_onboarding(bus: BusDep) -> RestartResponse:
    """Clear the ``onboarding_complete`` flag.

    Called by the dashboard "Restart onboarding" button. The saved
    bot token and super-admin list are intentionally left in place
    so the wizard's resume logic (Step 1 view mode, prefilled admin
    rows) picks them up again — a deployer can re-confirm a setup
    without re-typing the chat ids.

    Clears both the canonical key (``onboarding.complete``) and
    the legacy v0 key (``telegram.onboarding_complete``) so a
    deployer's previous setting doesn't accidentally keep them
    out of the wizard. The legacy key is read-only on the
    status path; ``/restart`` is the one place that writes a
    delete for it too.
    """
    if control_store.enabled():
        control_store.delete(bus, "onboarding.complete")
        return RestartResponse(ok=True)

    try:
        bus.settings_book.delete(key="onboarding.complete")
        bus.settings_book.delete(key="telegram.onboarding_complete")
    except Exception:  # pragma: no cover — disk / permission errors
        logger.exception("failed to clear onboarding_complete flag")
        return RestartResponse(ok=False)
    logger.info("onboarding marked incomplete (restart)")
    return RestartResponse(ok=True)


@router.post("/verify-bot", response_model=VerifyBotResponse)
async def verify_bot(payload: VerifyBotRequest) -> VerifyBotResponse:
    """Verify a Telegram bot token via ``getMe``.

    Delegates to :func:`magi.channels.telegram.bot.verify_token`
    so the TG API interaction stays inside the channel package.
    """
    token = payload.token.strip()
    if not token:
        return VerifyBotResponse(ok=False, error="Token is empty")

    try:
        username = await tg_bot.verify_token(token)
        return VerifyBotResponse(ok=True, username=username)
    except RuntimeError as exc:
        return VerifyBotResponse(ok=False, error=str(exc))


@router.post("/save-bot", response_model=SaveBotResponse)
async def save_bot(
    payload: SaveBotRequest, bus: BusDep, workers: WorkersDep,
) -> SaveBotResponse:
    """Persist the verified bot token + username into the settings table.

    The frontend guarantees the token passed ``verify-bot`` immediately
    before this call. Re-verifying here would cost an extra Telegram
    round-trip for no gain; the only way a stale token lands in the
    DB is if the deployer's network is hijacked between clicks.
    """
    if control_store.enabled():
        from magi.channels.api.control_runtime import bootstrap_telegram
        try:
            await bootstrap_telegram(payload.token, payload.username)
            control_store.set(bus, "telegram.bot_username", payload.username)
        except Exception as exc:
            logger.exception("failed to configure root runtime telegram")
            return SaveBotResponse(ok=False, error=str(exc))
        return SaveBotResponse(ok=True)

    try:
        bus.settings_book.set(key="telegram.bot_token", value=payload.token)
        bus.settings_book.set(key="telegram.bot_username", value=payload.username)
    except Exception as exc:  # pragma: no cover — disk / permission errors
        logger.exception("failed to write settings")
        return SaveBotResponse(ok=False, error=str(exc))

    # Best-effort: hot-restart the TG polling worker so it picks up
    # the newly-saved token without requiring a manual node restart.
    # ``stop_worker`` is a no-op when the worker isn't currently
    # running, so this also covers the cold-start case.
    try:
        await workers.stop_worker("tg")
        await workers.start_worker("tg")
    except Exception:
        logger.exception("failed to auto-start telegram bot after save-bot")

    logger.info(
        "bot token saved",
        extra={"username": payload.username},
    )
    return SaveBotResponse(ok=True)


@router.post("/verify-admin", response_model=VerifyAdminResponse)
async def verify_admin(payload: VerifyAdminRequest, bus: BusDep) -> VerifyAdminResponse:
    """Backward-compat alias for ``send-admin-code`` — older frontend
    versions call ``/verify-admin`` to get the bot to send the user a
    test message. The new code-based flow uses ``/send-admin-code``
    and ``/verify-admin-code`` instead.
    """
    return await _send_admin_code_inner(bus, SendAdminCodeRequest(tgid=payload.tgid))


@router.post("/send-admin-code", response_model=SendAdminCodeResponse)
async def send_admin_code(payload: SendAdminCodeRequest, bus: BusDep) -> SendAdminCodeResponse:
    """Generate a one-time 6-digit code, store it in ``settings``, and
    send it to the bound TG chat via the saved bot. The user reads the
    code in Telegram, types it back into the wizard, and
    ``/verify-admin-code`` confirms it matches.

    Requires ``telegram.bot_token`` to already be saved (step 2). The
    bot must have been started by the user (``/start`` in TG) —
    otherwise Telegram's privacy mode may reject the message.
    """
    return await _send_admin_code_inner(bus, payload)


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


async def _send_admin_code_inner(bus: Bus, payload: SendAdminCodeRequest) -> SendAdminCodeResponse:
    """Shared body for the public endpoints and the back-compat alias.

    D.28: this path runs BEFORE the wizard has bound an Contact
    row to a uid, so the channel dispatcher (which resolves
    ``uid → im_id``) can't be used here. The TG-side send
    helper lives in :mod:`magi.channels.telegram.bot`
    (:func:`magi.channels.telegram.bot.send_text_raw`) — we call it directly. Once
    ``/save-admin`` lands, the operator IS bound to a uid and
    every subsequent outbound goes through the dispatcher.
    """
    from datetime import datetime, timezone

    if control_store.enabled():
        delivery_address = payload.tgid.strip()
        if not delivery_address.lstrip("-").isdigit():
            return SendAdminCodeResponse(ok=False, error="tgid must be numeric")
        previous = control_store.get(bus, f"telegram.verify_code.{delivery_address}")
        if previous:
            try:
                if datetime.now(timezone.utc).timestamp() - float(json.loads(previous).get("last_sent_at", 0)) < _RESEND_COOLDOWN_SECONDS:
                    return SendAdminCodeResponse(ok=False, error="Wait before requesting a new code.")
            except (TypeError, ValueError, json.JSONDecodeError):
                pass
        code = _generate_code()
        now = datetime.now(timezone.utc)
        control_store.set(bus, f"telegram.verify_code.{delivery_address}", json.dumps({"code": code, "expires_at": now.timestamp() + _CODE_TTL_SECONDS, "last_sent_at": now.timestamp()}))
        from magi.channels.api.control_runtime import send_telegram
        try:
            await send_telegram(int(delivery_address), f"Your MAGI setup code is: <code>{code}</code>")
        except Exception as exc:
            control_store.delete(bus, f"telegram.verify_code.{delivery_address}")
            return SendAdminCodeResponse(ok=False, error=f"Telegram send failed: {exc}")
        return SendAdminCodeResponse(ok=True, expires_in=_CODE_TTL_SECONDS)

    bot_token = bus.settings_book.get(key="telegram.bot_token")
    if not bot_token:
        return SendAdminCodeResponse(
            ok=False,
            error="Bot token not saved yet — finish step 2 first.",
        )

    delivery_address = payload.tgid.strip()
    if not delivery_address.lstrip("-").isdigit():
        return SendAdminCodeResponse(ok=False, error="tgid must be numeric")
    # ``delivery_address`` is the per-channel IM id (a TG chat
    # id today; opaque to domain code). The settings key embeds
    # it as a string — that's the historical schema; we keep it
    # verbatim so existing state files keep working.

    # Resend cooldown — a stuck-network retry or impatient user must
    # wait before we spam the chat with another code. We check the
    # LAST SENT timestamp stored in settings (separate from the code's
    # own expiry so the cooldown applies even if the previous code is
    # already expired).
    previous = bus.settings_book.get(key=f"telegram.verify_code.{delivery_address}")
    if previous:
        # Safe default for the JSON-parse-failed branch — the cooldown
        # gate (next ``if prev_sent_at``) will short-circuit anyway,
        # but Pylance needs ``prev_data`` defined for the later
        # ``prev_data.get("expires_at", 0)`` access to type-check.
        prev_data: dict[str, object] = {}
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
    bus.settings_book.set(
        key=f"telegram.verify_code.{delivery_address}",
        value=json.dumps(
            {
                "code": code,
                "issued_at": issued_at.replace(microsecond=0).isoformat(),
                "expires_at": expires_at,
                "last_sent_at": issued_at.timestamp(),
            }
        ),
    )

    # Push the code. Try the running bot first (covers the
    # post-onboarding case); if the bot isn't running yet (the
    # common onboarding case), send directly via Telegram HTTP
    # API using the saved token.
    text = (
        f"Your MAGI setup code is: <code>{code}</code>\n\n"
        f"Enter this code in the MAGI admin wizard to "
        f"verify your chat. The code expires in "
        f"{_CODE_TTL_SECONDS // 60} minutes."
    )

    try:
        await tg_bot.send_text_raw(bot_token, int(delivery_address), text)
    except Exception as exc:
        bus.settings_book.delete(key=f"telegram.verify_code.{delivery_address}")
        return SendAdminCodeResponse(
            ok=False, error=f"Telegram send failed: {exc}",
        )

    logger.info(
        "admin verification code sent",
        extra={"ttl_seconds": _CODE_TTL_SECONDS},
    )
    return SendAdminCodeResponse(ok=True, expires_in=_CODE_TTL_SECONDS)


@router.post("/verify-admin-code", response_model=VerifyAdminCodeResponse)
async def verify_admin_code(payload: VerifyAdminCodeRequest, bus: BusDep) -> VerifyAdminCodeResponse:
    """Check the code the user typed against the one we sent to the
    candidate chat. On success:

    1. **Expiry check** — code must be within the 5-minute TTL.
    2. **One-shot** — burn the code on any attempt (success, mismatch,
       or expiry) so a wrong-guess attacker can't grind through the
       6^6 space against a still-valid code.
    3. **Don't persist yet** — the operator's per-channel delivery
       address is recorded only after they finish the wizard via
       ``save_admin`` (the Contact row + ``role='admin'``
       is the single source of truth). Verify just proves
       ownership; the operator still has to confirm the
       final admin list.
    4. Fetch a display name via ``getChat`` for the frontend.
    """
    from datetime import datetime, timezone

    if control_store.enabled():
        delivery_address, code = payload.tgid.strip(), payload.code.strip()
        raw = control_store.get(bus, f"telegram.verify_code.{delivery_address}")
        if not raw or not code.isdigit() or len(code) != 6:
            return VerifyAdminCodeResponse(ok=False, error="No valid code sent to this chat.")
        control_store.delete(bus, f"telegram.verify_code.{delivery_address}")
        try:
            stored = json.loads(raw)
            if datetime.now(timezone.utc).timestamp() >= float(stored.get("expires_at", 0)) or stored.get("code") != code:
                return VerifyAdminCodeResponse(ok=False, error="Code does not match or expired")
        except (TypeError, ValueError, json.JSONDecodeError):
            return VerifyAdminCodeResponse(ok=False, error="Stored code is corrupt")
        return VerifyAdminCodeResponse(ok=True)

    delivery_address = payload.tgid.strip()
    code = payload.code.strip()
    if not code.isdigit() or len(code) != 6:
        return VerifyAdminCodeResponse(ok=False, error="Code must be 6 digits")

    raw = bus.settings_book.get(key=f"telegram.verify_code.{delivery_address}")
    if not raw:
        return VerifyAdminCodeResponse(
            ok=False,
            error="No code sent to this chat — request a new one.",
        )

    try:
        payload_data = json.loads(raw)
    except (ValueError, TypeError):
        logger.warning(
            "stored verify code is not valid JSON for chat=%s",
            delivery_address,
        )
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

    if not expires_at or now_ts >= expires_at:
        bus.settings_book.delete(key=f"telegram.verify_code.{delivery_address}")
        return VerifyAdminCodeResponse(
            ok=False,
            error="Code expired — request a new one.",
        )

    # Burn on any path that gets past expiry (mismatch, success,
    # anything) so the code can't be re-tried by an attacker.
    bus.settings_book.delete(key=f"telegram.verify_code.{delivery_address}")

    if stored != code:
        return VerifyAdminCodeResponse(ok=False, error="Code does not match")

    # The code match is the proof-of-ownership; we don't persist
    # the delivery address here. The wizard's ``save_admin``
    # step (the final "Save" button) is what writes admin
    # rows to the ``contacts`` table — that path is the single
    # source of truth for "who's an admin". Persisting at this
    # point would create Contact rows that the operator might
    # later remove via save_admin's diff step, doubling the
    # work for no gain.

    bot_token = bus.settings_book.get(key="telegram.bot_token") or ""
    display_name = await tg_bot.get_chat_name_raw(bot_token, int(delivery_address))
    logger.info(
        "admin chat verified via code",
        extra={"display_name": display_name},
    )
    return VerifyAdminCodeResponse(ok=True, display_name=display_name)


@router.post("/save-admin", response_model=SaveAdminResponse)
async def save_admin(payload: SaveAdminRequest, bus: BusDep) -> SaveAdminResponse:
    """Replace the super-admin set with the verified list.

    Each entry becomes an :class:`Contact` row with
    ``role='admin'`` and a bound TG chat (via the channel
    dispatcher, D.28), living under no team (the
    "unassigned" scope). Display name is resolved via Telegram
    ``getChat`` so the dashboard can show the operator's
    handle instead of their TG numeric id without a second
    round-trip per row.

    Side effects on each call:
      - Any prior ``Contact`` with ``role='admin'`` whose
        bound TG chat isn't in the new list is **deleted**
        (these rows were created by onboarding too; they
        have no business data so dropping is safe).
      - Any prior ``Contact`` whose bound TG chat matches an
        entry gets its ``role`` flipped to ``admin`` even if
        it was previously a regular contact (this handles
        the rare case where someone was first added to the
        company, then promoted to admin).

    No settings key is written; the Contact table is the
    single source of truth for "who's an admin". The auth
    gate (``_is_admin_or_assigned_contact`` in
    ``contacts.py``) reads exclusively from this table.
    """

    if control_store.enabled():
        try:
            telegram_ids = sorted({int(value.strip()) for value in payload.tgids if value.strip()})
        except ValueError:
            return SaveAdminResponse(ok=False, error="tgid must be numeric")
        if not telegram_ids:
            return SaveAdminResponse(ok=False, error="At least one tgid required")
        root = bus.magis_book.get_root() if bus.magis_book else None
        if root is None or bus.magis_admins_book is None:
            return SaveAdminResponse(ok=False, error="Genesis MAGIS is not initialized")
        for tg_id in telegram_ids:
            if not any(row.uid == tg_id for row in bus.magis_admins_book.list_for_magis(magis_id=root.id)):
                bus.magis_admins_book.add(uid=tg_id, magis_id=root.id)
        return SaveAdminResponse(ok=True, count=len(telegram_ids))

    cleaned = sorted({c.strip() for c in payload.tgids if c.strip()})
    if not cleaned:
        return SaveAdminResponse(ok=False, error="At least one tgid required")
    # Each entry must be a TG-compatible integer (possibly
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

    # Display name resolution runs in parallel for all ids.
    bot_token = bus.settings_book.get(key="telegram.bot_token") or ""
    display_names: dict[int, str | None] = {}
    if parsed_ids:
        results = await asyncio.gather(
            *(tg_bot.get_chat_name_raw(bot_token, c) for c in parsed_ids),
            return_exceptions=True,
        )
        for cid, name in zip(parsed_ids, results):
            if isinstance(name, BaseException):
                # getChat failed (timeout, 4xx, etc.). The admin
                # row is still created — we just fall back to
                # the chat id as the display. The row's name
                # field holds the human-readable label (see
                # below).
                display_names[cid] = None
            else:
                display_names[cid] = name

    # The bus service performs the diff (drop admins not in the
    # new set, upsert/insert the rest) and returns the resulting
    # contact ids in input order.
    try:
        new_ct_ids = bus.contacts_book.replace_admin_set(
            [(cid, display_names.get(cid)) for cid in parsed_ids]
        )
    except Exception as exc:
        logger.exception("failed to write admin contacts")
        return SaveAdminResponse(ok=False, error=str(exc))

    logger.info("admins saved", extra={"count": len(cleaned)})
    return SaveAdminResponse(ok=True, count=len(cleaned))
