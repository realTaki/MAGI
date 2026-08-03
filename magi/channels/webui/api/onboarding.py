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
           ``role='admin'``, a TG binding (via the channel dispatcher),
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
import os
from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import select

from magi.db import ControlOperator, MAGIS, MAGISAdmin, require_state_dir
from magi.channels.telegram import bot as tg_bot
from magi.channels import Channel
from magi.channels.webui import control_store

logger = logging.getLogger("magi.api.onboarding")

router = APIRouter(tags=["onboarding"])

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


class CompleteRequest(BaseModel):
    pass


class CompleteResponse(BaseModel):
    ok: bool
    error: str | None = None


class RestartRequest(BaseModel):
    pass


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
    if control_store.enabled():
        from magi.db.magis import open_magis_session
        with open_magis_session() as session:
            root = session.scalar(select(MAGIS).where(MAGIS.parent_id.is_(None)).order_by(MAGIS.id))
            admins = session.scalars(select(MAGISAdmin).where(MAGISAdmin.magis_id == root.id)).all() if root else []
        username = control_store.get("telegram.bot_username")
        return OnboardingStatus(
            bot_saved=bool(username), bot_username=username,
            super_admins_count=len(admins), super_admins=[str(a.telegram_id) for a in admins],
            onboarding_complete=(control_store.get("onboarding.complete") or "").lower() in {"true", "1"},
            login_methods=["tg_code"] if admins else [],
            mode="with_tg" if admins else None,
        )
    from magi.db import Contact, open_session
    from magi.db.settings import state_get
    from magi.bus.models.magis.auth_credential import AuthCredential

    state_dir = _state_dir()
    bot_username = state_get(state_dir, "telegram.bot_username")

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
        with open_session() as session:
            admin_rows = list(session.scalars(
                select(Contact).where(Contact.admin == 1)
            ).all())
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
                has_password = session.scalar(
                    select(AuthCredential).where(
                        AuthCredential.uid == first.id,
                        AuthCredential.kind == "password",
                    )
                ) is not None
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
        login_methods=login_methods,
        mode=chosen_mode,
    )


@router.post("/set-admin-password", response_model=SetAdminPasswordResponse)
async def set_admin_password_onboarding(
    payload: SetAdminPasswordRequest,
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

    from magi.channels.webui.api import password_utils

    try:
        new_hash = password_utils.hash_password(payload.password)
    except ValueError as exc:
        return SetAdminPasswordResponse(ok=False, error=str(exc))

    from magi.db import Contact, open_session
    from magi.bus.models.magis.auth_credential import AuthCredential

    with open_session() as session:
        existing_admin = session.scalar(
            select(Contact).where(Contact.admin == 1).order_by(Contact.id)
        )
        if existing_admin is None:
            # First admin — fresh insert. ``role='assigned'``
            # mirrors the typical single-MAGI operator.
            row = Contact(name=name, admin=True, role="assigned")
            session.add(row)
            session.flush()
            admin_uid = row.id
        else:
            # Subsequent admin renames (operator re-entered
            # the wizard) reuse the row so chat history
            # survives. If a password row already exists,
            # we overwrite the hash — the operator is
            # explicitly setting a new password.
            existing_admin.name = name
            admin_uid = existing_admin.id

        # Upsert the password credential.
        cred = session.scalar(
            select(AuthCredential).where(
                AuthCredential.uid == admin_uid,
                AuthCredential.kind == "password",
            )
        )
        if cred is None:
            session.add(AuthCredential(
                uid=admin_uid, kind="password", secret_hash=new_hash,
            ))
        else:
            cred.secret_hash = new_hash
            cred.updated_at = datetime.utcnow()

        session.commit()

    logger.info(
        "onboarding: admin password set",
        extra={"uid": admin_uid, "name": name},
    )
    return SetAdminPasswordResponse(ok=True, admin_uid=admin_uid)


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
    if control_store.enabled():
        control_store.set("onboarding.complete", "true")
        return CompleteResponse(ok=True)
    from magi.bus import bootstrap

    bus = bootstrap(_state_dir())

    # 1. Stamp one nudge per current admin. Helper is
    #    idempotent — re-running (e.g. retry after failure,
    #    second wizard pass after /restart) is a no-op for any
    #    admin that already has an open row.
    try:
        admins = bus.contacts.list_admins()
        inserted = sum(
            bus.action_item.ensure_llm_credentials_item(owner_uid=admin.id)
            for admin in admins
        )
    except Exception as exc:  # pragma: no cover — DB failure
        logger.exception(
            "complete: action-item insert failed (%d admins, %d inserted before error)",
            len(admins) if 'admins' in locals() else 0, inserted if 'inserted' in locals() else 0,
        )
        return CompleteResponse(ok=False)

        # 1.5. Branch-aware credential check. A WebUI-only
    #      wizard must have at least one admin with a
    #      password credential before ``/complete`` is
    #      allowed to flip the flag — otherwise the
    #      operator couldn't sign in. The TG branch
    #      relies on the existing admin-row check above.
    if admins:
        has_password = bus.auth.has_password_credentials([admin.id for admin in admins])
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
        bus.settings.set("onboarding.complete", "true")
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
    without re-typing the chat ids.

    Clears both the canonical key (``onboarding.complete``) and
    the legacy v0 key (``telegram.onboarding_complete``) so a
    deployer's previous setting doesn't accidentally keep them
    out of the wizard. The legacy key is read-only on the
    status path; ``/restart`` is the one place that writes a
    delete for it too.
    """
    if control_store.enabled():
        control_store.delete("onboarding.complete")
        return RestartResponse(ok=True)
    from magi.db.settings import state_delete

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
async def save_bot(payload: SaveBotRequest) -> SaveBotResponse:
    """Persist the verified bot token + username into the settings table.

    The frontend guarantees the token passed ``verify-bot`` immediately
    before this call. Re-verifying here would cost an extra Telegram
    round-trip for no gain; the only way a stale token lands in the
    DB is if the deployer's network is hijacked between clicks.
    """
    if control_store.enabled():
        from magi.channels.webui.control_runtime import bootstrap_telegram
        try:
            await bootstrap_telegram(payload.token, payload.username)
            control_store.set("telegram.bot_username", payload.username)
        except Exception as exc:
            logger.exception("failed to configure root runtime telegram")
            return SaveBotResponse(ok=False, error=str(exc))
        return SaveBotResponse(ok=True)
    from magi.db.settings import state_set

    state_dir = _state_dir()
    try:
        state_set(state_dir, "telegram.bot_token", payload.token)
        state_set(state_dir, "telegram.bot_username", payload.username)
    except Exception as exc:  # pragma: no cover — disk / permission errors
        logger.exception("failed to write settings")
        return SaveBotResponse(ok=False, error=str(exc))

    # Best-effort: bring up the TG polling bot immediately after
    # token save so login-code delivery works without requiring a
    # manual node restart.
    try:
        if tg_bot.get_telegram_bot() is None:
            tg_bot.start_bot(state_dir)
    except Exception:
        logger.exception("failed to auto-start telegram bot after save-bot")

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
    return await _send_admin_code_inner(SendAdminCodeRequest(tgid=payload.tgid))


@router.post("/send-admin-code", response_model=SendAdminCodeResponse)
async def send_admin_code(payload: SendAdminCodeRequest) -> SendAdminCodeResponse:
    """Generate a one-time 6-digit code, store it in ``settings``, and
    send it to the bound TG chat via the saved bot. The user reads the
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
        previous = control_store.get(f"telegram.verify_code.{delivery_address}")
        if previous:
            try:
                if datetime.now(timezone.utc).timestamp() - float(json.loads(previous).get("last_sent_at", 0)) < _RESEND_COOLDOWN_SECONDS:
                    return SendAdminCodeResponse(ok=False, error="Wait before requesting a new code.")
            except (TypeError, ValueError, json.JSONDecodeError):
                pass
        code = _generate_code()
        now = datetime.now(timezone.utc)
        control_store.set(f"telegram.verify_code.{delivery_address}", json.dumps({"code": code, "expires_at": now.timestamp() + _CODE_TTL_SECONDS, "last_sent_at": now.timestamp()}))
        from magi.channels.webui.control_runtime import send_telegram
        try:
            await send_telegram(int(delivery_address), f"Your MAGI setup code is: <code>{code}</code>")
        except Exception as exc:
            control_store.delete(f"telegram.verify_code.{delivery_address}")
            return SendAdminCodeResponse(ok=False, error=f"Telegram send failed: {exc}")
        return SendAdminCodeResponse(ok=True, expires_in=_CODE_TTL_SECONDS)
    from magi.db.settings import state_get, state_set

    bot_token = state_get(_state_dir(), "telegram.bot_token")
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
    previous = state_get(_state_dir(), f"telegram.verify_code.{delivery_address}")
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
        f"telegram.verify_code.{delivery_address}",
        json.dumps(
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
        from magi.db.settings import state_delete
        state_delete(_state_dir(), f"telegram.verify_code.{delivery_address}")
        return SendAdminCodeResponse(
            ok=False, error=f"Telegram send failed: {exc}",
        )

    logger.info(
        "admin verification code sent",
        extra={"ttl_seconds": _CODE_TTL_SECONDS},
    )
    return SendAdminCodeResponse(ok=True, expires_in=_CODE_TTL_SECONDS)


@router.post("/verify-admin-code", response_model=VerifyAdminCodeResponse)
async def verify_admin_code(payload: VerifyAdminCodeRequest) -> VerifyAdminCodeResponse:
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
        raw = control_store.get(f"telegram.verify_code.{delivery_address}")
        if not raw or not code.isdigit() or len(code) != 6:
            return VerifyAdminCodeResponse(ok=False, error="No valid code sent to this chat.")
        control_store.delete(f"telegram.verify_code.{delivery_address}")
        try:
            stored = json.loads(raw)
            if datetime.now(timezone.utc).timestamp() >= float(stored.get("expires_at", 0)) or stored.get("code") != code:
                return VerifyAdminCodeResponse(ok=False, error="Code does not match or expired")
        except (TypeError, ValueError, json.JSONDecodeError):
            return VerifyAdminCodeResponse(ok=False, error="Stored code is corrupt")
        return VerifyAdminCodeResponse(ok=True)
    from magi.db.settings import state_get

    delivery_address = payload.tgid.strip()
    code = payload.code.strip()
    if not code.isdigit() or len(code) != 6:
        return VerifyAdminCodeResponse(ok=False, error="Code must be 6 digits")

    raw = state_get(_state_dir(), f"telegram.verify_code.{delivery_address}")
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

    from magi.db.settings import state_delete
    if not expires_at or now_ts >= expires_at:
        state_delete(_state_dir(), f"telegram.verify_code.{delivery_address}")
        return VerifyAdminCodeResponse(
            ok=False,
            error="Code expired — request a new one.",
        )

    # Burn on any path that gets past expiry (mismatch, success,
    # anything) so the code can't be re-tried by an attacker.
    state_delete(_state_dir(), f"telegram.verify_code.{delivery_address}")

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

    bot_token = state_get(_state_dir(), "telegram.bot_token") or ""
    display_name = await tg_bot.get_chat_name_raw(bot_token, int(delivery_address))
    logger.info(
        "admin chat verified via code",
        extra={"display_name": display_name},
    )
    return VerifyAdminCodeResponse(ok=True, display_name=display_name)


async def _fetch_display_name(delivery_address: str) -> str | None:
    """Resolve a TG chat display name via raw HTTP API.
    Fails silently — returns ``None`` on any error.
    """
    from magi.db.settings import state_get

    bot_token = state_get(_state_dir(), "telegram.bot_token") or ""
    return await tg_bot.get_chat_name_raw(bot_token, int(delivery_address))


def _now_iso() -> str:
    """UTC ISO timestamp without microseconds — the settings table is
    text-only and we don't need sub-second precision for a 5-min TTL."""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@router.post("/save-admin", response_model=SaveAdminResponse)
async def save_admin(payload: SaveAdminRequest) -> SaveAdminResponse:
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
        from magi.db.magis import open_magis_session
        with open_magis_session() as session:
            root = session.scalar(select(MAGIS).where(MAGIS.parent_id.is_(None)).order_by(MAGIS.id))
            if root is None:
                return SaveAdminResponse(ok=False, error="Genesis MAGIS is not initialized")
            existing = session.scalars(select(MAGISAdmin).where(MAGISAdmin.magis_id == root.id)).all()
            wanted = set(telegram_ids)
            for operator in existing:
                if operator.telegram_id not in wanted:
                    session.delete(operator)
            known = {operator.telegram_id: operator for operator in existing}
            for telegram_id in telegram_ids:
                if telegram_id not in known:
                    session.add(MAGISAdmin(magis_id=root.id, telegram_id=telegram_id, display_name=f"Admin {telegram_id}"))
            session.commit()
        return SaveAdminResponse(ok=True, count=len(telegram_ids))
    from magi.db import Contact, open_session
    from magi.channels import dispatcher as channel_dispatcher

    state_dir = _state_dir()
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
    from magi.db.settings import state_get

    bot_token = state_get(_state_dir(), "telegram.bot_token") or ""
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

    try:
        new_ct_ids: list[int] = []
        with open_session() as session:
            # 1) Existing admin rows not in the new list → delete
            #    (these are onboarding-created shells; no
            #    business data so dropping is safe). Match by
            #    the legacy ``Contact.telegram_id`` column
            #    (the read-cache the inbound handler still
            #    uses) — admins without a TG binding can't
            #    ever have been put here by onboarding, but
            #    we tolerate them for parity with the API
            #    surface.
            # Identify existing operators by the new
            # ``admin=True`` boolean (replaces the pre-2024
            # ``role='admin'`` query — admin was split from
            # role when the two concepts collided on the
            # served user who is also an operator).
            existing_admins = session.scalars(
                select(Contact).where(Contact.admin == 1)
            ).all()
            new_id_set = set(parsed_ids)
            for old in existing_admins:
                if old.telegram_id is None or old.telegram_id not in new_id_set:
                    session.delete(old)

            # 2) Each new chat → ensure a Contact row
            #    exists with admin=True. New operators are
            #    stamped ``role='assigned'`` (matches the
            #    migration's data semantics: a fresh operator
            #    is the served user of this single-operator
            #    install). Promote existing regular contacts
            #    in the rare case the chat was already
            #    bound.
            #
            # The actual IM binding is the channel adapter's
            # job (D.28): we call ``dispatcher.bind_im_id``
            # AFTER the with block to write
            # ``user_im_bindings`` (canonical) and sync
            # ``Contact.telegram_id`` (legacy read-cache).
            for cid in parsed_ids:
                contact = session.scalar(
                    select(Contact).where(Contact.telegram_id == cid)
                )
                if contact is None:
                    contact = Contact(
                        name=display_names[cid] or f"Admin {cid}",
                        display_name=display_names[cid],
                        role="assigned",
                        admin=True,
                    )
                    session.add(contact)
                    session.flush()
                else:
                    contact.admin = True
                    # If the row was previously not the
                    # served user (role='guest'), also flip
                    # it to 'assigned' — the operator of a
                    # single-operator install IS the served
                    # user. Multi-operator installs can
                    # manually re-set role afterwards if
                    # needed.
                    if contact.role == "guest":
                        contact.role = "assigned"
                    if display_names[cid]:
                        contact.name = display_names[cid]
                        if not contact.display_name:
                            contact.display_name = display_names[cid]
                new_ct_ids.append(contact.id)
            session.commit()
    except Exception as exc:
        logger.exception("failed to write admin contacts")
        return SaveAdminResponse(ok=False, error=str(exc))

    # D.28: bind each new admin's TG chat id through the
    # channel dispatcher. The adapter writes
    # ``user_im_bindings`` (canonical) and syncs the
    # legacy ``Contact.telegram_id`` column for the bot's
    # inbound handler. Done outside the with block because
    # the dispatcher opens its own session (nested BEGIN
    # IMMEDIATE inside an outer transaction would deadlock
    # SQLite).
    from magi.channels import dispatcher as channel_dispatcher
    for ct_id, cid in zip(new_ct_ids, parsed_ids):
        try:
            channel_dispatcher.bind_im_id(ct_id, Channel.TG, str(cid))
        except Exception:
            # Best-effort: the Contact row is already
            # created with role=admin. If the binding
            # write fails (e.g. FK or DB hiccup), the
            # admin can re-bind via the API later.
            logger.exception(
                "save_admin: dispatcher.bind_im_id failed "
                "for ct_id=%s cid=%s",
                ct_id, cid,
            )

    logger.info("admins saved", extra={"count": len(cleaned)})
    return SaveAdminResponse(ok=True, count=len(cleaned))
