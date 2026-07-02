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
      1. super-admin allowlist (role = ``"admin"``)        — log only for now
      2. ``telegram.user.<chat_id>.employee_id`` is set    — route through
         the agent loop using that employee's LLM credentials (falls back
         to system default if the employee has none).
      3. first-touch, never seen before                    — record as
         GUEST and send the chat_id discovery reply.
      4. recorded GUEST                                    — same reply
         (so retries work).
      5. any other recorded role (OTHER_EMPLOYEE / BOT)   — log only;
         no per-role handler yet.
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

    # 1. ADMIN: in the allowlist. The admin chat will grow real
    # admin commands (C6+); for v0 we just log so the
    # operator can see the bot is alive.
    if chat_id in admins:
        logger.info(
            "telegram: admin message (no-op until C6)",
            extra={"chat_id": chat_id, "display_name": display_name},
        )
        return

    # 2. Bound employee: ``telegram.user.<chat_id>.employee_id``
    # is set by the bind-telegram flow (C2 lands the full
    # flow; v0 supports a manual ``bind`` set via a small
    # admin endpoint or direct meta write). The chat_id is
    # the TG side of the binding; the value is the row id in
    # ``employees``.
    employee_id_str = _get_user_employee_id(state_dir, chat_id)
    if employee_id_str is not None:
        try:
            employee_id = int(employee_id_str)
        except (TypeError, ValueError):
            logger.warning(
                "telegram: malformed employee_id binding for chat %s: %r",
                chat_id, employee_id_str,
            )
            employee_id = None
        if employee_id is not None:
            await _handle_employee_message(
                update, state_dir, chat_id, employee_id, display_name,
            )
            return

    # 3. First-time contact: record the user. The first-touch
    # message always gets the discovery reply; a second
    # message from the same unknown chat_id also gets the
    # same reply (the "recorded GUEST" path below).
    if _get_user_role(state_dir, chat_id) is None:
        _record_user(state_dir, chat_id, "GUEST", display_name=display_name)
        logger.info(
            "telegram: first-touch, recorded as GUEST",
            extra={"chat_id": chat_id, "display_name": display_name},
        )

    # 4. Recorded GUEST (re-send or first-touch): tell them
    # their chat_id and how to reach the admin. The reply
    # is the same on every message so the discovery flow
    # works even if the user retries after a flaky send.
    role = _get_user_role(state_dir, chat_id) or "GUEST"
    if role == "GUEST":
        await update.effective_message.reply_text(
            UNKNOWN_USER_REPLY.format(chat_id=chat_id),
            parse_mode="HTML",
        )
        return

    # 5. Any other recorded role (OTHER_EMPLOYEE / BOT) —
    # no per-role handler yet; log so we can see them in
    # the dev container's stdout.
    logger.info(
        "telegram: %s role, no handler yet", role,
        extra={"chat_id": chat_id, "display_name": display_name},
    )


def _get_user_employee_id(state_dir: str, chat_id: str) -> str | None:
    """Read ``telegram.user.<chat_id>.employee_id`` from the
    meta table.

    Set by the binding flow (C2's admin endpoint or, for v0,
    by direct meta write). ``None`` when unbound; the caller
    falls through to the GUEST path. The value is a string
    of the integer ``employees.id`` so we don't need to
    think about encoding.
    """
    from magi.runtime.state.settings import state_get

    return state_get(state_dir, f"telegram.user.{chat_id}.employee_id")


def _set_user_employee_id(
    state_dir: str, chat_id: str, employee_id: int | None
) -> None:
    """Write or clear ``telegram.user.<chat_id>.employee_id``.

    Used by the binding admin endpoint and (later) the
    in-TG ``/start <code>`` flow. ``employee_id=None``
    clears the binding.
    """
    from magi.runtime.state.settings import state_delete, state_set

    key = f"telegram.user.{chat_id}.employee_id"
    if employee_id is None:
        state_delete(state_dir, key)
    else:
        state_set(state_dir, key, str(employee_id))


async def _handle_employee_message(
    update: Update,
    state_dir: str,
    chat_id: str,
    employee_id: int,
    display_name: str | None,
) -> None:
    """Route a message from a bound employee through the agent loop.

    Looks up the employee's LLM credentials (provider + api_key)
    from the ORM, falls back to the system default if the
    employee has none, calls :func:`magi.runtime.agent.handle_message`,
    and replies with the LLM's text. On any agent error the
    user gets a friendly fallback string; the real error is
    audited.

    The function is intentionally short: all the interesting
    logic (credential resolution, audit, fallback) lives in
    ``handle_message``. The TG channel's job is just to
    translate a message event into a string in, string out.
    """
    from magi.runtime.agent import handle_message
    from magi.runtime.state.orm import Employee, open_session

    text = update.effective_message.text or ""
    if not text.strip():
        # Sticker / photo / voice / etc — the agent loop
        # only handles text in v0. Acknowledge so the user
        # knows we got it but explain the limitation.
        await update.effective_message.reply_text(
            "我暂时只支持文字消息，等 C4 加上多模态再试。",
        )
        return

    # Look up the employee's LLM config. SQLAlchemy's
    # session lifecycle: open one for this single query.
    employee_provider: str | None = None
    employee_api_key: str | None = None
    employee_separated: bool = False
    employee_name: str | None = None
    try:
        with open_session() as session:
            emp = session.get(Employee, employee_id)
            if emp is None:
                logger.warning(
                    "telegram: chat %s bound to missing employee %s; "
                    "treating as unbound",
                    chat_id, employee_id,
                )
                _set_user_employee_id(state_dir, chat_id, None)
                await update.effective_message.reply_text(
                    UNKNOWN_USER_REPLY.format(chat_id=chat_id),
                    parse_mode="HTML",
                )
                return
            employee_provider = emp.provider
            employee_api_key = emp.api_key
            employee_separated = emp.separated_at is not None
            employee_name = emp.name
    except Exception as e:
        logger.exception(
            "telegram: ORM read failed for employee %s: %s", employee_id, e,
        )
        await update.effective_message.reply_text(
            "服务暂时不可用，请稍后再试。",
        )
        return

    if employee_separated:
        # Separated employees can't chat with their EVE —
        # the org marked them as 离职, so the agent is
        # paused. Admin can restore via the dashboard.
        await update.effective_message.reply_text(
            f"你的账号（{employee_name}）已标记为离职。如需恢复，请联系管理员。",
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