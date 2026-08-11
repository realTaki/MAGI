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
from datetime import UTC
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from magi.bus import Bus
from magi.bus.library.local.actionItemBook import SOURCE_PROACTIVE
from magi.bus.library.local.contactBook import ROLE_ASSIGNED
from magi.channels.api import control_store
from magi.channels.api.dependencies import BusDep, WorkersDep
from magi.channels.telegram import bot as tg_bot

logger = logging.getLogger("magi.api.onboarding")

router = APIRouter(tags=["onboarding"])

# A second, narrower router that exposes ONLY the password-set step
# on the runtime. The runtime must serve this so the singleton webui
# (which runs in control-plane mode and has no per-runtime
# ``contacts_book``) can forward the wizard's first-admin write to the
# runtime's local Bus. Mounting the full ``router`` on the runtime
# would expose handlers like ``save-bot`` whose writes target
# ``control_settings_book`` — a Book the runtime does not own.
runtime_onboarding_router = APIRouter(tags=["onboarding-runtime"])


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

    The wizard collects **two** operator identities for the
    first launch:

      * ``admin_*`` — the Genesis-level admin. Lives in
        :class:`MagisAdminBook` (MAGIS DB) so the same
        person can sign in to every MAGI in the Genesis
        Society.
      * ``assigned_*`` — the person served by ``eva-000``
        (the runtime being onboarded). Their :class:`Contact`
        carries ``role='assigned'`` in the runtime's local
        SQLite so they can sign in to eva-000 only.

    Both rows carry a password hash on the runtime's local
    SQLite so login works without a Telegram binding.
    ``admin=True`` is **not** set on either row — admin is
    a MAGIS-level concept and is recorded only in
    :class:`MagisAdminBook` (``magis_admins.contact_id``,
    opaque integer reference).
    """

    admin_name: str = Field(min_length=1, max_length=120)
    admin_password: str = Field(min_length=8, max_length=256)
    assigned_name: str = Field(min_length=1, max_length=120)
    assigned_password: str = Field(min_length=8, max_length=256)


class SetAdminPasswordResponse(BaseModel):
    ok: bool
    error: str | None = None
    admin_contact_id: int | None = None
    assigned_contact_id: int | None = None


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
            [str(a.contact_id) for a in bus.magis_admins_book.list_for_magis(magis_id=root_id)]
            if bus.magis_admins_book is not None and root_id is not None
            else []
        )
        username = control_store.get(bus, "telegram.bot_username")
        return OnboardingStatus(
            bot_saved=bool(username),
            bot_username=username,
            super_admins_count=len(admins),
            super_admins=admins,
            onboarding_complete=(control_store.get(bus, "onboarding.complete") or "").lower()
            in {"true", "1"},
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
            if admin.tgid is not None:
                admins.append(str(admin.tgid))
        # ``login_methods`` summarises the wizard's
        # owner (the first admin row). The wizard
        # only ever sets up one admin's credentials
        # during onboarding, so the first row is the
        # source of truth.
        if admin_rows:
            first = admin_rows[0]
            has_password = bus.contacts_book.get_password_hash(contact_id=first.id) is not None
            if has_password:
                login_methods.append("password")
            if first.tgid is not None:
                login_methods.append("tg_code")
            chosen_mode = (
                "with_tg" if first.tgid else ("webui_only" if has_password else None)
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
    onboarding_complete = str(complete_raw or old_raw or "").strip().lower() in ("true", "1")

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
    """WebUI-only onboarding step 2: create the first two operators.

    The TG wizard's step 2 collects TG chat ids via
    :func:`save_admin`; this endpoint is the parallel flow for
    operators who picked "WebUI only" in step 1. Two
    identities land on the same wizard:

      * **Genesis admin** (``admin_name`` / ``admin_password``)
        — recorded in MAGIS-shared :class:`MagisAdminBook` so
        they can sign in to **every** MAGI in the Genesis
        Society. Their ``contact_id`` is an opaque integer
        reference into the runtime's local ``contacts`` table.
      * **eva-000's assigned user**
        (``assigned_name`` / ``assigned_password``) — a
        per-MAGI identity living only in the runtime's local
        SQLite as a ``Contact`` row with
        ``role='assigned'``. They can sign in to eva-000
        only.

    Both rows live in the **runtime's local SQLite** (the
    runtime owns ``contacts`` and ``contacts.password_hash``)
    and **neither** sets ``Contact.admin=True`` — admin is a
    MAGIS-level concept and lives in ``magis_admins``.

    Storage split:

      * Runtime (per-MAGI SQLite) → upserts two ``Contact``
        rows, each with ``role='assigned'``, writes a
        password hash on each.
      * Webui (MAGIS DB) → registers a ``magis_admins`` row
        for the admin contact only; the assigned contact
        stays runtime-local.

    The runtime responds first with both contact ids; the
    webui then writes the MAGIS-side ``magis_admins`` row.
    Re-entering the wizard is idempotent — both sides
    reuse previous rows so chat history survives.
    """
    admin_name = payload.admin_name.strip()
    assigned_name = payload.assigned_name.strip()
    if not admin_name:
        return SetAdminPasswordResponse(ok=False, error="admin_name is required")
    if not assigned_name:
        return SetAdminPasswordResponse(ok=False, error="assigned_name is required")

    if control_store.enabled():
        runtime_response = await _forward_set_admin_password_to_runtime(bus, payload)
        if runtime_response is None:
            return SetAdminPasswordResponse(
                ok=False,
                error="runtime unreachable; cannot set admin password",
            )
        if not runtime_response.ok:
            return runtime_response
        admin_cid = runtime_response.admin_contact_id
        assigned_cid = runtime_response.assigned_contact_id
        if admin_cid is None or assigned_cid is None:
            return SetAdminPasswordResponse(
                ok=False,
                error="runtime did not return both contact ids",
            )
        magis_id = _register_magis_admin(bus, contact_id=int(admin_cid))
        if magis_id is None:
            return SetAdminPasswordResponse(
                ok=False,
                error="Genesis MAGIS not initialised; cannot register admin",
            )
        logger.info(
            "onboarding: admin + assigned set (webui)",
            extra={
                "admin_contact_id": admin_cid,
                "assigned_contact_id": assigned_cid,
                "magis_id": magis_id,
                "admin_name": admin_name,
                "assigned_name": assigned_name,
            },
        )
        return SetAdminPasswordResponse(
            ok=True,
            admin_contact_id=admin_cid,
            assigned_contact_id=assigned_cid,
        )

    # Single-process / runtime path — no MAGIS DB to register against.
    admin_cid = _upsert_local_contact(bus, admin_name, slot="admin")
    assigned_cid = _upsert_local_contact(
        bus,
        assigned_name,
        slot="assigned",
        skip_existing_contact_id=admin_cid if admin_name == assigned_name else None,
    )
    from magi.channels.api import password_utils

    try:
        admin_hash = password_utils.hash_password(payload.admin_password)
        assigned_hash = password_utils.hash_password(payload.assigned_password)
    except ValueError as exc:
        return SetAdminPasswordResponse(ok=False, error=str(exc))
    bus.contacts_book.set_password_hash(contact_id=admin_cid, password_hash=admin_hash)
    bus.contacts_book.set_password_hash(contact_id=assigned_cid, password_hash=assigned_hash)
    logger.info(
        "onboarding: admin + assigned set (runtime)",
        extra={
            "admin_contact_id": admin_cid,
            "assigned_contact_id": assigned_cid,
            "admin_name": admin_name,
            "assigned_name": assigned_name,
        },
    )
    return SetAdminPasswordResponse(
        ok=True,
        admin_contact_id=admin_cid,
        assigned_contact_id=assigned_cid,
    )


def _register_magis_admin(bus: Bus, *, contact_id: int) -> int | None:
    """Register ``contact_id`` as an admin of the root MAGIS.

    Returns the root MAGIS id on success, ``None`` when the
    MAGIS Books are not available. Idempotent: a re-entered
    wizard reuses the existing MagisAdmin row.
    """
    if bus.magis_book is None or bus.magis_admins_book is None:
        logger.warning("onboarding: webui admin skipped — MAGIS books unavailable")
        return None
    root = bus.magis_book.get_root()
    if root is None:
        logger.warning("onboarding: webui admin skipped — Genesis MAGIS not initialised")
        return None
    if bus.magis_admins_book.is_admin_for(contact_id=contact_id):
        return root.id
    bus.magis_admins_book.add(contact_id=contact_id, magis_id=root.id)
    logger.info(
        "onboarding: webui MagisAdmin registered",
        extra={"contact_id": contact_id, "magis_id": root.id},
    )
    return root.id


def _upsert_local_contact(
    bus: Bus,
    name: str,
    *,
    slot: str = "assigned",
    skip_existing_contact_id: int | None = None,
) -> int:
    """Create a Contact named ``name`` if absent, else reuse in place.

    The new row carries ``role='assigned'`` and **does not** set
    ``admin=True`` — admin is a MAGIS-level concept and lives in
    :class:`MagisAdminBook`. Contact rows created here are only the
    "person" identity; the wizard / auth layer joins them with
    ``magis_admins`` (webui) or reads the password hash directly
    (runtime login).

    ``slot`` distinguishes wizard steps that may collide by name
    ("Taki" appearing as both Genesis admin AND per-MAGI
    assigned): the admin slot always lands on its own row by
    suffixing when needed, the assigned slot reuses an existing
    row only if its id is NOT the one just minted for the admin
    in the same wizard call.
    """
    contacts = bus.contacts_book
    for existing in contacts.list_all():
        if existing.name == name and int(existing.id) != (skip_existing_contact_id or -1):
            return int(existing.id)
    base = name
    if slot == "admin" and any(c.name == base for c in contacts.list_all()):
        # Avoid colliding with the just-allocated assigned row by
        # appending a slot suffix. The display_name keeps the
        # operator's chosen name; the row's `name` is unique.
        base = f"{name} (admin)"
    try:
        created = contacts.add(name=base, role=ROLE_ASSIGNED, display_name=name)
    except ValueError:
        base = f"{base}-onboarding"
        created = contacts.add(name=base, role=ROLE_ASSIGNED, display_name=name)
    return int(created.id)


# Expose the same handler on the runtime-only router so the runtime
# can serve the forwarded call from the singleton webui.
runtime_onboarding_router.add_api_route(
    "/set-admin-password",
    set_admin_password_onboarding,
    methods=["POST"],
    response_model=SetAdminPasswordResponse,
)


async def _forward_set_admin_password_to_runtime(
    bus: Bus, payload: SetAdminPasswordRequest
) -> SetAdminPasswordResponse | None:
    """Forward ``/api/onboarding/set-admin-password`` to the runtime.

    Returns ``None`` when no runtime is reachable (legacy
    control deployments that run inline). Otherwise
    returns the runtime's response.
    """
    runtimes = bus.runtime_state_book
    if runtimes is None:
        return None
    runtime = next(
        (r for r in runtimes.list_all() if r.base_url and r.port_released_at is None),
        None,
    )
    if runtime is None or not runtime.base_url:
        return None
    import httpx

    url = f"{runtime.base_url.rstrip('/')}/api/onboarding/set-admin-password"
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            upstream = await client.post(url, json=payload.model_dump())
    except httpx.HTTPError as exc:
        logger.warning(
            "onboarding: runtime forward failed",
            extra={"runtime_id": runtime.runtime_id, "url": url, "error": str(exc)},
        )
        return SetAdminPasswordResponse(
            ok=False,
            error=f"runtime unreachable: {exc}",
        )
    try:
        data = upstream.json()
    except ValueError:
        return SetAdminPasswordResponse(
            ok=False,
            error=f"runtime returned non-JSON ({upstream.status_code})",
        )
    return SetAdminPasswordResponse(
        ok=bool(data.get("ok")),
        error=data.get("error"),
        admin_contact_id=data.get("admin_contact_id"),
        assigned_contact_id=data.get("assigned_contact_id"),
    )


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
    _NUDGE_DESC = "切到「Contacts」,找到自己的档案,把 Provider 和 API Key 填上。"
    _NUDGE_URL = "/dashboard?tab=organization"
    try:
        admins = bus.contacts_book.list_admins()
        inserted = 0
        for admin in admins:
            existing_open = [
                row
                for row in bus.action_items_book.list_actions(
                    owner_contact_id=admin.id,
                    include_completed=False,
                    source=SOURCE_PROACTIVE,
                )
                if row.title == _NUDGE_TITLE
            ]
            if existing_open:
                continue
            existing_done = [
                row
                for row in bus.action_items_book.list_actions(
                    owner_contact_id=admin.id,
                    include_completed=True,
                    source=SOURCE_PROACTIVE,
                )
                if row.title == _NUDGE_TITLE and row.completed_at is not None
            ]
            if existing_done:
                continue
            bus.action_items_book.add(
                contact_id=admin.id,
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
            bus.contacts_book.get_password_hash(contact_id=admin.id) is not None for admin in admins
        )
        # ``has_tg`` = any admin has a tgid.
        has_tg = any(admin.tgid for admin in admins)
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
    payload: SaveBotRequest,
    bus: BusDep,
    workers: WorkersDep,
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
    sent = await _send_admin_code_inner(bus, SendAdminCodeRequest(tgid=payload.tgid))
    # The legacy shape has no ``expires_in``; carry over only the fields
    # this endpoint's contract declares.
    return VerifyAdminResponse(ok=sent.ok, error=sent.error)


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
    row to a contact_id, so the channel dispatcher (which resolves
    ``contact_id → im_id``) can't be used here. The TG-side send
    helper lives in :mod:`magi.channels.telegram.bot`
    (:func:`magi.channels.telegram.bot.send_text_raw`) — we call it directly. Once
    ``/save-admin`` lands, the operator IS bound to a contact_id and
    every subsequent outbound goes through the dispatcher.
    """
    from datetime import datetime

    if control_store.enabled():
        delivery_address = payload.tgid.strip()
        if not delivery_address.lstrip("-").isdigit():
            return SendAdminCodeResponse(ok=False, error="tgid must be numeric")
        previous = control_store.get(bus, f"telegram.verify_code.{delivery_address}")
        if previous:
            try:
                if (
                    datetime.now(UTC).timestamp()
                    - float(json.loads(previous).get("last_sent_at", 0))
                    < _RESEND_COOLDOWN_SECONDS
                ):
                    return SendAdminCodeResponse(
                        ok=False, error="Wait before requesting a new code."
                    )
            except (TypeError, ValueError, json.JSONDecodeError):
                pass
        code = _generate_code()
        now = datetime.now(UTC)
        control_store.set(
            bus,
            f"telegram.verify_code.{delivery_address}",
            json.dumps(
                {
                    "code": code,
                    "expires_at": now.timestamp() + _CODE_TTL_SECONDS,
                    "last_sent_at": now.timestamp(),
                }
            ),
        )
        from magi.channels.api.control_runtime import send_telegram

        try:
            await send_telegram(
                int(delivery_address), f"Your MAGI setup code is: <code>{code}</code>"
            )
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
        # ``Any`` reflects the JSON-decoded dict honestly: its values
        # may be anything, and the downstream ``float(...)`` casts do
        # the real validation.
        prev_data: dict[str, Any] = {}
        try:
            prev_data = json.loads(previous)
            prev_sent_at = float(prev_data.get("last_sent_at", 0))
        except (TypeError, ValueError, json.JSONDecodeError):
            prev_sent_at = 0
        if prev_sent_at:
            elapsed = datetime.now(UTC).timestamp() - prev_sent_at
            if elapsed < _RESEND_COOLDOWN_SECONDS:
                remaining = int(_RESEND_COOLDOWN_SECONDS - elapsed)
                # How much life the old code still has (may already be
                # 0 if the previous send was close to its expiry).
                prev_expires = float(prev_data.get("expires_at", 0))
                prev_remaining = max(0, int(prev_expires - datetime.now(UTC).timestamp()))
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
    issued_at = datetime.now(UTC)
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
            ok=False,
            error=f"Telegram send failed: {exc}",
        )

    logger.info(
        "admin verification code sent",
        extra={"ttl_seconds": _CODE_TTL_SECONDS},
    )
    return SendAdminCodeResponse(ok=True, expires_in=_CODE_TTL_SECONDS)


@router.post("/verify-admin-code", response_model=VerifyAdminCodeResponse)
async def verify_admin_code(
    payload: VerifyAdminCodeRequest, bus: BusDep
) -> VerifyAdminCodeResponse:
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
    from datetime import datetime

    if control_store.enabled():
        delivery_address, code = payload.tgid.strip(), payload.code.strip()
        raw = control_store.get(bus, f"telegram.verify_code.{delivery_address}")
        if not raw or not code.isdigit() or len(code) != 6:
            return VerifyAdminCodeResponse(ok=False, error="No valid code sent to this chat.")
        control_store.delete(bus, f"telegram.verify_code.{delivery_address}")
        try:
            stored = json.loads(raw)
            if (
                datetime.now(UTC).timestamp() >= float(stored.get("expires_at", 0))
                or stored.get("code") != code
            ):
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
    now_ts = datetime.now(UTC).timestamp()

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
        # Control-plane mirror of the single-MAGI path below. ``magis_admins.contact_id``
        # is a ``ForeignKey("contacts.id")`` — the schema rejects raw telegram
        # chat ids. We therefore upsert a local :class:`Contact` per telegram
        # id (same shape the non-control path produces via
        # :meth:`ContactBook.replace_admin_set`) and link it into
        # ``magis_admins`` with the Contact's PK. Existing admin rows whose
        # bound chat is no longer in the set are demoted on both sides so
        # the "replace the admin set" semantics carry over.
        try:
            tgids = sorted({int(value.strip()) for value in payload.tgids if value.strip()})
        except ValueError:
            return SaveAdminResponse(ok=False, error="tgid must be numeric")
        if not tgids:
            return SaveAdminResponse(ok=False, error="At least one tgid required")
        root = bus.magis_book.get_root() if bus.magis_book else None
        if root is None or bus.magis_admins_book is None or bus.contacts_book is None:
            return SaveAdminResponse(ok=False, error="Genesis MAGIS is not initialized")

        # 1. Demote existing admins whose bound chat is no longer in the set.
        for existing in bus.magis_admins_book.list_for_magis(magis_id=root.id):
            if existing.contact_id in tgids:
                continue
            bus.magis_admins_book.remove(contact_id=existing.contact_id, magis_id=root.id)
            contact = bus.contacts_book.get(contact_id=existing.contact_id)
            if contact is not None and contact.tgid not in tgids:
                bus.contacts_book.update(contact_id=existing.contact_id, admin=False)

        # 2. Upsert a Contact per telegram id and link it into magis_admins.
        for tg_id in tgids:
            contact = bus.contacts_book.get_by_telegram(tgid=tg_id)
            if contact is None:
                contact = bus.contacts_book.add(
                    name=f"tg-{tg_id}",
                    display_name=f"tg-{tg_id}",
                    tgid=tg_id,
                    role=ROLE_ASSIGNED,
                    admin=True,
                )
            elif not contact.admin:
                bus.contacts_book.update(contact_id=contact.id, admin=True)
            if not bus.magis_admins_book.is_admin_for(contact_id=contact.id):
                bus.magis_admins_book.add(contact_id=contact.id, magis_id=root.id)
        return SaveAdminResponse(ok=True, count=len(tgids))

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
        for cid, name in zip(parsed_ids, results, strict=False):
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
        bus.contacts_book.replace_admin_set([(cid, display_names.get(cid)) for cid in parsed_ids])
    except Exception as exc:
        logger.exception("failed to write admin contacts")
        return SaveAdminResponse(ok=False, error=str(exc))

    logger.info("admins saved", extra={"count": len(cleaned)})
    return SaveAdminResponse(ok=True, count=len(cleaned))
