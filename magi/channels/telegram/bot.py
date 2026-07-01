"""Telegram channel — bootstrap a python-telegram-bot listener.

C0/C1 behaviour: only the "first-touch" message handler is wired up.
When **anyone other than a registered super admin** sends the bot a
message (including the first ``/start``), we reply with their chat_id
and a "contact the admin" message. That way unprivileged users can
discover their own chat_id to hand to the deployer. Once they're in
``telegram.super_admins`` the same handler is a no-op (logs only).

C3 will replace this with a real agent-loop dispatcher: per-admin
routing, audit hooks, conversation buffer, etc.

Concurrency: the bot runs in a **daemon thread** with its own asyncio
loop (``Application.run_polling`` is blocking). It co-exists with the
uvicorn asyncio loop on the main thread without any coordination
needed — each thread does its own I/O.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from typing import Optional

from telegram import Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters

logger = logging.getLogger("magi.channels.telegram.bot")


#: Message sent to anyone who DMs the bot but isn't in the super-admin
#: allowlist. ``{chat_id}`` is filled with the sender's TG chat_id so
#: they can pass it to the admin to be onboarded.
UNKNOWN_USER_REPLY = (
    "👋 Hi — you're not in MAGI's super-admin list yet.\n\n"
    "Your Telegram chat_id is: <code>{chat_id}</code>\n\n"
    "Please contact the MAGI admin and share this ID so they can add "
    "your permissions. Once that's done, message me anything and I'll "
    "route you to the right person."
)


def _load_super_admins(state_dir: str) -> set[str]:
    """Read the super-admin allowlist from settings. Returns a set of
    stringified chat_ids. Tolerant of missing/garbage rows."""
    from magi.runtime.state.settings import state_get

    raw = state_get(state_dir, "telegram.super_admins")
    if not raw:
        return set()
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        logger.warning("telegram.super_admins is not valid JSON; treating as empty")
        return set()
    if not isinstance(parsed, list):
        return set()
    return {str(x) for x in parsed}


def _get_user_role(state_dir: str, chat_id: str) -> str | None:
    """Return the recorded role for a chat_id, or None if not yet seen."""
    from magi.runtime.state.settings import state_get

    return state_get(state_dir, f"telegram.user.{chat_id}.role")


def _record_user(
    state_dir: str,
    chat_id: str,
    role: str,
    display_name: str | None = None,
) -> None:
    """Persist a per-chat_id role (and display_name if given).

    No-op if the same role is already recorded — keeps first_seen stable
    for re-tries. Adding a different role for the same chat_id would
    overwrite (rare; usually means an admin changed roles in settings).
    """
    from magi.runtime.state.settings import state_set

    existing = _get_user_role(state_dir, chat_id)
    if existing != role:
        state_set(state_dir, f"telegram.user.{chat_id}.role", role)
    if display_name:
        from magi.runtime.state.settings import state_get

        current_name = state_get(state_dir, f"telegram.user.{chat_id}.display_name")
        if current_name != display_name:
            state_set(state_dir, f"telegram.user.{chat_id}.display_name", display_name)


async def _on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle any inbound message from any chat.

    The bot recognises five roles (ADMIN, ASSIGNED_EMPLOYEE,
    OTHER_EMPLOYEE, BOT, GUEST) but only the GUEST path is implemented
    for C0/C1: a sender we don't yet know about is auto-recorded as
    GUEST and gets a "here's your chat_id, contact the admin" reply.
    The other four roles are routed only as a no-op log line — the real
    agent-loop dispatcher (per-admin command, per-employee EVE, etc.)
    lands in C3.
    """
    if update.effective_chat is None or update.effective_message is None:
        return
    chat_id = str(update.effective_chat.id)
    display_name = (
        update.effective_chat.first_name
        or update.effective_chat.username
        or update.effective_chat.title
    )

    state_dir = os.environ.get("MAGI_STATE_DIR", "/workspace/memories")
    admins = _load_super_admins(state_dir)

    # ADMIN: in the allowlist. C1+ will wire actual admin commands; for
    # now we just log.
    if chat_id in admins:
        logger.info(
            "telegram: admin message (no-op until C3)",
            extra={"chat_id": chat_id, "display_name": display_name},
        )
        return

    # Everyone else: resolve the recorded role (default GUEST if unseen).
    role = _get_user_role(state_dir, chat_id) or "GUEST"

    # First-time contact: record the user. Future C1+ code will set
    # explicit roles here (ASSIGNED_EMPLOYEE / OTHER_EMPLOYEE / BOT)
    # based on the employees table; for now everything not in the admin
    # list is GUEST.
    if _get_user_role(state_dir, chat_id) is None:
        _record_user(state_dir, chat_id, "GUEST", display_name=display_name)
        logger.info(
            "telegram: first-touch, recorded as GUEST",
            extra={"chat_id": chat_id, "display_name": display_name},
        )

    if role == "GUEST":
        # C0 behaviour: tell them their chat_id + how to reach admin.
        # The four non-GUEST roles are future work; for now we just
        # log so we can see them in the dev container's stdout.
        await update.effective_message.reply_text(
            UNKNOWN_USER_REPLY.format(chat_id=chat_id),
            parse_mode="HTML",
        )
        return

    # Non-GUEST role. No-op until C3+ routes per role.
    logger.info(
        "telegram: %s role, no handler yet (C3+)", role,
        extra={"chat_id": chat_id, "display_name": display_name},
    )


def start_bot(state_dir: str) -> Optional[threading.Thread]:
    """Start the Telegram bot in a daemon thread. Returns the thread, or
    ``None`` if no bot token is saved yet (so the user hasn't completed
    step 1 of the onboarding wizard).

    Implementation note: ``Application.run_polling`` is not safe to call
    from a non-main thread (it tries to install asyncio signal handlers,
    which Python only allows in the main thread). We use the async API
    directly — ``initialize`` / ``start`` / ``updater.start_polling`` —
    and keep the loop alive with an ``asyncio.Event`` that never gets
    set. The daemon thread is killed when the process exits.
    """
    from magi.runtime.state.settings import state_get

    token = state_get(state_dir, "telegram.bot_token")
    if not token:
        logger.info(
            "telegram: no bot token saved yet — channel idle until onboarding completes"
        )
        return None

    username = state_get(state_dir, "telegram.bot_username")
    application = Application.builder().token(token).build()
    application.add_handler(MessageHandler(filters.ALL, _on_message))

    async def _run_forever() -> None:
        await application.initialize()
        await application.start()
        await application.updater.start_polling()
        # Park on an Event that never gets set. The loop exits when the
        # process is shutting down (which kills the daemon thread).
        await asyncio.Event().wait()

    def _thread_target() -> None:
        asyncio.run(_run_forever())

    thread = threading.Thread(
        target=_thread_target,
        name="telegram-bot",
        daemon=True,
    )
    thread.start()
    logger.info(
        "telegram bot started",
        extra={"username": username, "state_dir": state_dir},
    )
    return thread