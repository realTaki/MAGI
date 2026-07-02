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

    Role resolution (in order):
      1. ``Employee.telegram_id == chat_id`` and role is
         ``"admin"`` — log only for v0; C6+ adds real admin
         commands.
      2. ``Employee.telegram_id == chat_id`` and role is
         ``"employee"`` / ``"assigned"`` — route through the
         agent loop using that employee's LLM credentials
         (falls back to system default if the employee has
         none).
      3. otherwise (no employee bound) — treat as GUEST and
         send the chat_id discovery reply. The role gate is
         decided per-MAGI-instance (the canonical state lives
         on the row, not in a meta key).

    The legacy ``telegram.user.<chat_id>.employee_id`` meta
    binding is **deprecated** — bindings now live on
    ``Employee.telegram_id``. The read path falls back to
    the meta for state files written before the unified
    table landed (C1.x), so a half-migrated state still
    works.
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

    # 1+2. Look up the bound employee. Single ORM read by
    # ``telegram_id`` covers both admin and employee roles;
    # the role decides what we do next.
    bound = _find_employee_by_telegram_id(state_dir, chat_id)
    if bound is not None:
        emp_id, emp_role, emp_name, emp_separated, emp_provider, emp_key = bound
        if emp_role == "admin":
            # The admin chat is growing real admin commands
            # in C6+; for v0 we just log so the operator
            # can see the bot is alive when an admin pings
            # it. We don't run the LLM on admin messages
            # because that would burn the operator's API
            # key on chitchat.
            logger.info(
                "telegram: admin message (no-op until C6)",
                extra={"chat_id": chat_id, "display_name": display_name},
            )
            return
        # ``employee`` / ``assigned`` / ``other`` — all
        # routed to the agent loop. ``other`` is reserved
        # for multi-instance (C6+) where it means "an
        # employee served by a different EVE"; the agent
        # still answers, just logged differently.
        await _handle_employee_message(
            update,
            state_dir,
            chat_id,
            emp_id,
            emp_name,
            display_name,
            emp_separated,
            emp_provider,
            emp_key,
        )
        return

    # 3. No employee bound — treat as GUEST.
    if _get_user_role(state_dir, chat_id) is None:
        _record_user(state_dir, chat_id, "GUEST", display_name=display_name)
        logger.info(
            "telegram: first-touch, recorded as GUEST",
            extra={"chat_id": chat_id, "display_name": display_name},
        )

    role = _get_user_role(state_dir, chat_id) or "GUEST"
    if role == "GUEST":
        await update.effective_message.reply_text(
            UNKNOWN_USER_REPLY.format(chat_id=chat_id),
            parse_mode="HTML",
        )
        return

    # Any other recorded role (OTHER_EMPLOYEE / BOT) —
    # no per-role handler yet; log so we can see them in
    # the dev container's stdout.
    logger.info(
        "telegram: %s role, no handler yet", role,
        extra={"chat_id": chat_id, "display_name": display_name},
    )


def _find_employee_by_telegram_id(
    state_dir: str, chat_id: str
) -> tuple[int, str, str, bool, str | None, str | None] | None:
    """Resolve a TG chat_id to its bound employee.

    Single ORM read on ``Employee.telegram_id``; returns
    ``(employee_id, role, name, separated, provider, api_key)``
    on hit, ``None`` when no row has the chat_id bound.
    The role is what the dispatcher uses to decide
    between admin / employee / GUEST handling — see
    :func:`_on_message`. ``provider`` / ``api_key`` are
    pre-resolved so :func:`_handle_employee_message` can
    dispatch to the LLM without a second round-trip.

    Falls back to the legacy ``telegram.user.<chat_id>.employee_id``
    meta key for state files written before the unified
    table landed (C1.x). The meta key is read-only here;
    bindings are now written through the
    ``PATCH /api/employees/{id}`` endpoint.
    """
    from sqlalchemy import select

    from magi.runtime.state.orm import Employee, open_session
    from magi.runtime.state.settings import state_get

    try:
        cid_int = int(chat_id)
    except (TypeError, ValueError):
        return None

    def _fields(e: Employee) -> tuple[int, str, str, bool, str | None, str | None]:
        return (
            e.id,
            e.role,
            e.name,
            e.separated_at is not None,
            e.provider,
            e.api_key,
        )

    try:
        with open_session() as session:
            emp = session.scalar(
                select(Employee).where(Employee.telegram_id == cid_int)
            )
            if emp is not None:
                return _fields(emp)
    except Exception:
        logger.exception(
            "telegram: ORM read failed resolving chat %s", chat_id,
        )

    # Legacy meta binding — only the employee_id is recorded,
    # so we re-read the row to get the role / name. The
    # meta key is left in place (the operator might still
    # have stale bindings) but writes go through the new
    # path.
    raw = state_get(state_dir, f"telegram.user.{chat_id}.employee_id")
    if not raw:
        return None
    try:
        legacy_emp_id = int(raw)
    except (TypeError, ValueError):
        return None
    try:
        with open_session() as session:
            emp = session.get(Employee, legacy_emp_id)
            if emp is None:
                return None
            return _fields(emp)
    except Exception:
        logger.exception(
            "telegram: legacy-meta ORM read failed for emp %s", legacy_emp_id,
        )
        return None


async def _handle_employee_message(
    update: Update,
    state_dir: str,
    chat_id: str,
    employee_id: int,
    employee_name: str,
    display_name: str | None,
    employee_separated: bool,
    employee_provider: str | None,
    employee_api_key: str | None,
) -> None:
    """Route a message from a bound employee through the agent loop.

    All the LLM credentials are pre-resolved by
    :func:`_find_employee_by_telegram_id` so this function
    is pure dispatch: text in, reply out, with the agent
    loop doing the audit + fallback. A separated employee
    gets a polite "you're 离职" reply and no LLM call.
    """
    from magi.runtime.agent import handle_message

    if employee_separated:
        # Separated employees can't chat with their EVE —
        # the org marked them as 离职, so the agent is
        # paused. Admin can restore via the dashboard.
        await update.effective_message.reply_text(
            f"你的账号（{employee_name}）已标记为离职。如需恢复，请联系管理员。",
        )
        return

    text = update.effective_message.text or ""
    if not text.strip():
        # Sticker / photo / voice / etc — the agent loop
        # only handles text in v0. Acknowledge so the user
        # knows we got it but explain the limitation.
        await update.effective_message.reply_text(
            "我暂时只支持文字消息，等 C4 加上多模态再试。",
        )
        return

    reply = await handle_message(
        state_dir,
        text=text,
        channel="tg",
        employee_id=employee_id,
        employee_provider=employee_provider,
        employee_api_key=employee_api_key,
    )

    # Telegram has a 4096-char message limit. The agent
    # loop's reply is well under that in practice, but a
    # long SOUL.md + verbose model could overflow. Truncate
    # defensively with a note so the user knows there's
    # more.
    if len(reply) > 4000:
        reply = reply[:3990] + "\n\n…(回复过长，已截断)"

    await update.effective_message.reply_text(reply)


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