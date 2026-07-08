"""Telegram channel — bootstrap a python-telegram-bot listener.

C0/C1 behaviour: only the "first-touch" message handler is wired up.
When **anyone other than a registered admin** sends the bot a
message (including the first ``/start``), we reply with their chat_id
and a "contact the admin" message. That way unprivileged users can
discover their own chat_id to hand to the deployer. Admin status
is determined by ``Employee.role='admin'`` (the unified table
written during onboarding) — there is no separate settings key for
the admin allowlist, by design: a single source of truth for
"who's an admin" avoids drift between ORM rows and meta blobs.

C3 will replace this with a real agent-loop dispatcher: per-admin
routing, audit hooks, conversation buffer, etc.

Concurrency: the bot runs in a **daemon thread** with its own asyncio
loop (``Application.run_polling`` is blocking). It co-exists with the
uvicorn asyncio loop on the main thread without any coordination
needed — each thread does its own I/O.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from typing import Optional

from telegram import Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters

logger = logging.getLogger("magi.channels.telegram.bot")

# All bot replies now live in ``magi/agent/prompts/bot_replies.yaml``
# — see that file for wording. The dispatchers below call
# :func:`magi.agent.prompts.load_bot_replies` once and look up
# templates by id. Keeping the templates out of code means an
# operator can tweak wording without touching Python. The
# lazy import + per-process cache in ``prompts/__init__.py``
# means a single YAML read per process; the dispatchers
# don't need to worry about the file system.
from magi.agent.prompts import load_bot_replies  # noqa: E402

# Loaded once per process. The dict is shared across
# messages, which is fine — values are templates, not
# state.
_BOT_REPLIES: dict[str, str] | None = None


def _replies() -> dict[str, str]:
    """Lazy loader that defers the YAML read to the first
    reply. Keeps the module importable even if the YAML
    file is temporarily missing during a deploy."""
    global _BOT_REPLIES
    if _BOT_REPLIES is None:
        _BOT_REPLIES = load_bot_replies()
    return _BOT_REPLIES


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
    # ``telegram_id``; the role decides what we do next.
    # Dispatch rules (per-MAGI perspective):
    #   - ``admin``    : real chat sender. The agent loop
    #                    runs; the admin's per-employee LLM
    #                    credentials (D.4+) are billed. v0
    #                    used to skip the LLM here ("admin
    #                    chat grows real admin commands in
    #                    C6+") but that left the admin
    #                    unable to use TG on mobile — fixed
    #                    so admin and assigned share the
    #                    same handler.
    #   - ``assigned`` : this MAGI serves the person. The
    #                    agent loop runs.
    #   - ``employee`` : another company employee. NOT
    #                    served by this MAGI. Cross-MAGI
    #                    access is a future concern; for
    #                    v0 we politely refuse and tell
    #                    them to talk to their own admin.
    #   - ``guest``    : not in this company at all. Same
    #                    refusal as ``employee`` so the
    #                    chat_id discovery path can be
    #                    surfaced ("here's your chat_id,
    #                    ask your admin to invite you").
    bound = _find_employee_by_telegram_id(state_dir, chat_id)
    if bound is not None:
        emp_id, emp_role, emp_name, emp_separated, emp_provider, emp_key = bound
        if emp_role not in ("admin", "assigned"):
            # ``employee`` / ``guest`` — refuse politely
            # without burning the LLM. The hint about
            # the chat_id is the same one the unknown-
            # chat path sends, so the user can pass
            # the id to whoever runs their company's
            # MAGI to get added.
            logger.info(
                "telegram: %s role not served by this MAGI; refusing",
                emp_role,
                extra={"chat_id": chat_id, "employee_id": emp_id},
            )
            await update.effective_message.reply_text(
                _replies()["cross_company_refusal"].format(
                    emp_name=emp_name, chat_id=chat_id,
                ),
            )
            return
        # ``admin`` and ``assigned`` both flow through the
        # same handler. The earlier "admin → no-op" branch
        # was a v0 guard against burning the admin's API key
        # on TG chitchat; once the admin has set per-employee
        # credentials (D.4+) they own that decision, and TG
        # chat-with-EVE is a real affordance for mobile
        # operators who don't want to open the WebUI.
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
    #
    # The chat_id discovery reply goes out to anyone not
    # bound to an Employee row; the ``Employee.telegram_id``
    # is the only source of truth for who's been "claimed".
    # There's nothing else to track here — historically we
    # wrote ``telegram.user.<chat_id>.{role,display_name}``
    # to settings, but those duplicated columns on the
    # Employee row are deprecated in favour of the unified
    # table and the operator has cleared them from settings.
    logger.info(
        "telegram: no employee bound, sending chat_id discovery",
        extra={"chat_id": chat_id, "display_name": display_name},
    )
    await update.effective_message.reply_text(
        _replies()["unknown_sender"].format(chat_id=chat_id),
        parse_mode="HTML",
    )
    return


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

    from magi.agent.db import Employee, open_session
    from magi.agent.db.settings import state_get

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

    Session lifecycle (D.10): TG now persists chat history
    the same way WebUI does — ``SessionStore`` writes
    one file per ``(chat_id, session_id)`` under
    ``<workspace>/memories/sessions/<chat_id>/<sid>.json``.
    Unlike WebUI (which has a sidebar "新对话" affordance),
    TG keeps **one session per chat_id forever** — the
    employee never asks for a fresh thread from this side.
    The session is auto-created on the first inbound
    message and reused for every subsequent turn in that
    chat, so the file grows into the employee's complete
    history with this EVE. Per-chat / per-topic session
    splits are a future C7+ affordance.
    """
    from magi.agent.loop import handle_message
    from magi.agent.sessions import (
        SessionMessage,
        SessionStore,
        new_session_id,
        utcnow_iso,
    )

    if employee_separated:
        # Separated employees can't chat with their EVE —
        # the org marked them as 离职, so the agent is
        # paused. Admin can restore via the dashboard.
        await update.effective_message.reply_text(
            _replies()["separated_employee"].format(employee_name=employee_name),
        )
        return

    text = update.effective_message.text or ""
    if not text.strip():
        # Sticker / photo / voice / etc — the agent loop
        # only handles text in v0. Acknowledge so the user
        # knows we got it but explain the limitation.
        await update.effective_message.reply_text(
            _replies()["non_text_message"],
        )
        return

    # -- read-receipt reaction (D.11) -----------------------------------
    # Set the configured emoji on the user's incoming message
    # *before* doing anything slow (session lookup, LLM call,
    # outbound append). The "I've seen this and I'm working on
    # it" signal should land while the operator is still
    # looking at the chat, not after the LLM has spent 30s
    # thinking.
    #
    # Failure mode: if the bot lacks ``set_message_reaction``
    # permission in this chat, Telegram raises ``Forbidden``
    # — we swallow it so a misconfigured chat doesn't kill
    # the whole inbound path. The operator can fix perms
    # later; the message still gets a real reply.
    from magi.channels.telegram.config import get_read_reaction_emoji
    try:
        reaction = get_read_reaction_emoji(state_dir)
        if reaction:
            await update.get_bot().set_message_reaction(
                chat_id=update.effective_chat.id,
                message_id=update.effective_message.message_id,
                reaction=reaction,
            )
    except Exception:
        logger.exception(
            "telegram: set_message_reaction failed (chat=%s msg=%s); "
            "continuing without read-receipt",
            update.effective_chat.id,
            update.effective_message.message_id,
        )

    # -- session lifecycle (D.10) --------------------------------------
    # Same shape as ``magi/channels/webui/api/chat.py``:
    #
    #   1. Try the *latest* session for this chat_id
    #      (``list_summaries(limit=1)`` returns most recent
    #      first). If one exists and isn't corrupt, reuse it —
    #      that's "one session per TG chat forever".
    #   2. Otherwise create a fresh one. First-message case
    #      also seeds the auto-title worker.
    #
    # The "reuse the last session" policy is intentionally
    # implicit: the TG client never tells the EVE "I want
    # a new thread"; if a future affordance (C7 command like
    # ``/new``) lands, it'll arrive here as an explicit
    # ``session_id = None`` and trigger the create branch.
    store = SessionStore(state_dir)
    session_id = _resolve_or_create_tg_session(store, chat_id, employee_id)

    # Inbound append — SQLite's per-statement atomicity replaces
    # the pre-D.18 per-session ``asyncio.Lock`` that used to
    # serialise against the auto-title worker.
    ts_in = utcnow_iso()
    try:
        post = store.append_messages(
            chat_id, session_id,
            [SessionMessage(
                role="user", text=text, ts=ts_in,
                message_id=new_session_id(),
            )],
        )
    except Exception:
        logger.exception(
            "telegram: failed to append user message for session %s",
            session_id,
        )
        # Fall through and still try the LLM call — losing
        # the audit trail is worse than the user seeing a
        # reply to a message we couldn't persist. They'll
        # just see the conversation "jump" in the file
        # history if they ever inspect it.
        post = None

    # First-user-message of a fresh thread → fire the same
    # auto-title worker the WebUI uses. The worker is keyed
    # by ``(chat_id, session_id)`` and uses its own per-
    # session lock; no TG-specific code needed.
    if post is not None and len(post.messages) == 1:
        try:
            from magi.agent.sessions.auto_title import enqueue_title_job
            await enqueue_title_job(
                chat_id=chat_id,
                session_id=session_id,
                employee_id=employee_id,
                employee_provider=employee_provider or "",
                employee_api_key=employee_api_key or "",
            )
        except Exception:
            logger.exception(
                "telegram: failed to enqueue title job for session %s",
                session_id,
            )

    # -- "typing…" indicator (D.14) --------------------------------------
    # TG clears the typing state automatically when our
    # ``reply_text`` lands, but only if the LLM reply comes
    # back within ~5s — past that, the client hides the
    # indicator. So we fire an immediate ``typing`` then
    # start a 4-second refresh loop in the background;
    # cancelled the moment ``handle_message`` returns.
    typing_stop = asyncio.Event()
    typing_task = asyncio.create_task(
        _typing_indicator_loop(
            update.get_bot(),
            update.effective_chat.id,
            typing_stop,
        ),
        name=f"tg-typing-{update.effective_chat.id}",
    )

    try:
        reply = await handle_message(
            state_dir,
            text=text,
            channel="tg",
            session_id=session_id,
            employee_id=employee_id,
            employee_provider=employee_provider,
            employee_api_key=employee_api_key,
        )
    finally:
        # Always cancel — success, error, exception.
        # ``reply_text`` below will let TG clear its UI;
        # without this stop the loop would keep pinging
        # the API every 4s until the 30s deadline.
        typing_stop.set()
        if not typing_task.done():
            typing_task.cancel()
            try:
                await typing_task
            except (asyncio.CancelledError, Exception):
                # ``_typing_indicator_loop`` swallows its own
                # errors; any exception reaching here is
                # unexpected but never actionable for the
                # operator — keep the reply path alive.
                pass

    # Telegram has a 4096-char message limit. The agent
    # loop's reply is well under that in practice, but a
    # long SOUL.md + verbose model could overflow. Truncate
    # defensively with a note so the user knows there's
    # more.
    if len(reply) > 4000:
        reply = reply[:3990] + "\n\n…(回复过长，已截断)"

    # Outbound append. Failure is logged but does NOT
    # raise — the TG user already got the reply via
    # ``reply_text`` below; a missing history row is worse
    # than a console error.
    ts_out = utcnow_iso()
    try:
        store.append_messages(
            chat_id, session_id,
            [SessionMessage(
                role="assistant", text=reply, ts=ts_out,
                message_id=new_session_id(),
            )],
        )
    except Exception:
        logger.exception(
            "telegram: failed to append assistant message for session %s",
            session_id,
        )

    await update.effective_message.reply_text(reply)


def _resolve_or_create_tg_session(
    store: "SessionStore",  # type: ignore[name-defined]  # noqa: F821
    chat_id: str,
    employee_id: int,
) -> str:
    """Return the session id to use for the next TG message.

    Policy (D.10): **one session per TG chat_id forever.**

    We look up the most recent session for ``chat_id`` and
    reuse it if it's still on disk and intact. Otherwise
    we mint a fresh one. This matches what the user
    experiences — they don't see a "new conversation" affordance
    in the TG client, so the EVE should treat the chat as
    a single ongoing thread.

    A corrupt session file (truncated JSON) is skipped and
    triggers creation of a new one — we lose the corrupt
    thread's history but don't crash the inbound handler.
    """
    try:
        summaries, _total = store.list_summaries(chat_id, limit=1, offset=0)
    except Exception:
        logger.exception(
            "telegram: session lookup failed for chat %s; minting fresh",
            chat_id,
        )
        summaries = []
    if summaries:
        # Verify the file is still readable (not corrupt).
        # ``list_summaries`` silently skips corrupt files,
        # so a hit here means the file parsed cleanly.
        try:
            store.get(chat_id, summaries[0].session_id)
            return summaries[0].session_id
        except Exception:
            logger.exception(
                "telegram: latest session %s for chat %s failed re-read; "
                "creating fresh",
                summaries[0].session_id, chat_id,
            )
    # No prior session (or it was corrupt) — mint a new one.
    sess = store.create(chat_id, employee_id=employee_id, channel="tg")
    return sess.session_id


# TG "typing…" action expires after ~5s, so we refresh
# every 4s while the LLM is thinking. The handler starts
# this loop in the background just before
# ``handle_message`` and signals it to stop via the returned
# ``stop_event`` — see ``_handle_employee_message``.
_TYPING_REFRESH_SECONDS = 4.0


async def _typing_indicator_loop(
    bot,
    chat_id: int,
    stop_event: "asyncio.Event",  # noqa: F821 — forward ref avoids an extra import
) -> None:
    """Send ``send_chat_action(typing)`` every 4s until
    ``stop_event`` is set, or 30s elapses, whichever comes
    first.

    The 30s ceiling is a defensive cap so a hung LLM doesn't
    cause this coroutine to spam the TG API indefinitely —
    a normal Anthropic reply lands in under 15s in practice;
    past 30s something is wrong and a fresh typing signal
    isn't going to fix the operator's wait.

    ``asyncio.CancelledError`` from the handler's task
    shutdown also exits cleanly — we don't ``raise`` it, we
    just return.
    """
    deadline = asyncio.get_event_loop().time() + 30.0
    while not stop_event.is_set():
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            return
        try:
            await bot.send_chat_action(
                chat_id=chat_id,
                action="typing",
            )
        except Exception:
            # ``Forbidden`` if the bot lost chat access, or a
            # transient network blip — neither should kill
            # the inbound path. Log once and stop trying;
            # spamming TG with retry attempts is worse than
            # silently dropping the typing indicator.
            logger.exception(
                "telegram: typing indicator failed (chat=%s); "
                "disabling further refreshes", chat_id,
            )
            return
        # Wait up to 4s OR until the reply arrives.
        try:
            await asyncio.wait_for(
                stop_event.wait(), timeout=_TYPING_REFRESH_SECONDS
            )
            # stop_event set → reply ready, exit.
            return
        except asyncio.TimeoutError:
            # Refresh period elapsed; loop and re-send.
            continue


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
    from magi.agent.db.settings import state_get

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