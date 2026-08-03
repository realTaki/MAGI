"""Telegram channel — bootstrap a python-telegram-bot listener.

C0/C1 behaviour: only the "first-touch" message handler is wired up.
When **anyone other than a registered admin** sends the bot a
message (including the first ``/start``), we reply with their tgid
and a "contact the admin" message. That way unprivileged users can
discover their own tgid to hand to the deployer. Admin status
is determined by ``Contact.role='admin'`` (the unified table
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
import threading

from telegram import Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters

logger = logging.getLogger("magi.channels.telegram.bot")

# All bot replies now live in ``magi/prompts/bot_replies.yaml``
# — see that file for wording. The dispatchers below call
# :func:`magi.prompts.load_bot_replies` once and look up
# templates by id. Keeping the templates out of code means an
# operator can tweak wording without touching Python. The
# lazy import + per-process cache in ``prompts/__init__.py``
# means a single YAML read per process; the dispatchers
# don't need to worry about the file system.
from magi.channels import Channel  # noqa: E402
from magi.constants import STATE_DIR  # noqa: E402
from magi.prompts import load_bot_replies  # noqa: E402

# Loaded once per process. The dict is shared across
# messages, which is fine — values are templates, not
# state.
_BOT_REPLIES: dict[str, str] | None = None

# Process-wide Telegram ``Bot`` instance registry. The daemon
# thread that runs ``Application.updater.start_polling`` owns
# the bot; the registry lets cross-cutting code (cron-fired
# tasks, post-deletion notifications, etc.) reach the same
# instance via a single setter/getter pair so we don't have
# to thread the bot reference through every module's
# parameter list. ``None`` whenever the bot isn't running
# (no token saved, fresh deploy, test environments).
# ``set_telegram_bot`` is called once at the start of
# :func:`start_bot`; ``clear_telegram_bot`` at the matching
# shutdown so a re-bind doesn't hold a stale reference.
_telegram_bot_instance: telegram.Bot | None = None
_telegram_bot_lock = threading.Lock()
_telegram_start_lock = threading.Lock()
_telegram_bot_thread: threading.Thread | None = None
_telegram_app: Application | None = None
_telegram_shutdown_event: asyncio.Event | None = None


def set_telegram_bot(bot) -> None:
    """Register the running ``telegram.Bot`` for cross-
    thread access (most notably :func:`channels.tasks.runner.
    execute_task`, which fires cron-driven rows into the
    operator's TG chat). Idempotent — replacing an existing
    instance just rebinds."""
    global _telegram_bot_instance
    with _telegram_bot_lock:
        _telegram_bot_instance = bot


def clear_telegram_bot() -> None:
    global _telegram_bot_instance
    with _telegram_bot_lock:
        _telegram_bot_instance = None


def get_telegram_bot():
    """Return the live ``telegram.Bot`` (or ``None`` when
    the bot isn't running). Used by the cron-runner to
    build a ``_tg_send_callback`` that lets the agent's
    ``send_message`` tool actually push replies to TG
    during a scheduled fire.
    """
    with _telegram_bot_lock:
        return _telegram_bot_instance


async def verify_token(bot_token: str) -> str:
    """Verify a bot token via Telegram ``getMe``. Returns the
    bot's username on success. Raises ``RuntimeError`` with a
    user-facing message on any failure.
    """
    import httpx

    url = f"https://api.telegram.org/bot{bot_token}/getMe"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url)
    except httpx.TimeoutException:
        raise RuntimeError("Telegram timed out")
    except httpx.RequestError as exc:
        raise RuntimeError(f"Network error: {exc}")

    if resp.status_code != 200:
        raise RuntimeError(f"Telegram returned HTTP {resp.status_code}")

    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(
            data.get("description", "Unknown error from Telegram")
        )
    username = (data.get("result") or {}).get("username")
    if not username:
        raise RuntimeError("Telegram response missing bot username")
    return username


async def send_text(chat_id: int, text: str) -> None:
    """Push ``text`` to the Telegram chat ``chat_id``.

    Requires the bot to be running (``get_telegram_bot()``
    is not None). Raises ``RuntimeError`` if not.

    For sending when the bot isn't running (e.g. during
    onboarding), use :func:`send_text_raw`.
    """
    bot = get_telegram_bot()
    if bot is None:
        raise RuntimeError(
            "telegram bot is not running; finish onboarding "
            "step 2 (bot token) first"
        )
    await bot.send_message(chat_id=chat_id, text=text)


async def send_text_auto(chat_id: int, text: str) -> None:
    """Send ``text`` to ``chat_id`` via raw HTTP, reading the
    bot token from settings internally.

    Does NOT use the python-telegram-bot Bot instance — avoids
    cross-thread event-loop issues. The raw HTTP approach is
    reliable inside Docker where the bot's event loop lives in
    a different thread.

    This is the preferred entry point for channel-internal
    callers (adapter, dispatcher). No caller outside
    ``magi/channels/telegram/`` should touch the bot token
    or raw HTTP directly.
    """
    from magi.bus import bootstrap
    import os

    bot_token = bootstrap(os.environ.get("MAGI_STATE_DIR", STATE_DIR)).settings.get("telegram.bot_token")
    if not bot_token:
        raise RuntimeError("telegram: no bot token saved; cannot send")
    await _send_via_raw_http(bot_token, chat_id, text)


async def _send_via_raw_http(bot_token: str, chat_id: int, text: str) -> None:
    """Internal raw HTTP send — used by ``send_text_raw`` and
    ``send_text_auto``. Raises ``RuntimeError`` on failure."""
    import httpx

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
            })
    except httpx.TimeoutException:
        raise RuntimeError("Telegram API timed out (network or firewall?)")
    except httpx.RequestError as exc:
        raise RuntimeError(f"Network error: {exc}")

    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(
            data.get("description", f"Telegram HTTP {resp.status_code}")
        )
    # Log the TG-assigned ``message_id`` so on-call can
    # cross-reference the operator's "I never got that
    # message" complaint against Telegram's server logs.
    # ``result`` is a Message object on success; fall back
    # to chat_id when the shape is unexpected.
    msg_id = None
    result = data.get("result")
    if isinstance(result, dict):
        msg_id = result.get("message_id")
    logger.info(
        "raw send to chat_id=%d message_id=%s",
        chat_id, msg_id,
    )


async def send_text_raw(bot_token: str, chat_id: int, text: str) -> None:
    """Send via raw HTTP — requires explicit ``bot_token``.

    Used during onboarding before the bot starts.
    """
    await _send_via_raw_http(bot_token, chat_id, text)


async def get_chat_name(chat_id: int) -> str | None:
    """Resolve a Telegram chat's display name via ``getChat``.

    Requires the bot to be running. Returns ``None`` on
    failure. For calls when the bot isn't running, use
    :func:`get_chat_name_raw`.
    """
    bot = get_telegram_bot()
    if bot is None:
        return None
    try:
        chat = await bot.get_chat(chat_id=chat_id)
    except Exception:
        return None
    name = getattr(chat, "first_name", None) or getattr(
        chat, "title", None
    ) or getattr(chat, "username", None)


async def get_chat_name_raw(bot_token: str, chat_id: int) -> str | None:
    """Resolve a Telegram chat's display name via raw HTTP API.

    Does not require the bot process. Returns ``None`` on failure.
    """
    import httpx

    url = f"https://api.telegram.org/bot{bot_token}/getChat"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json={"chat_id": chat_id})
        data = resp.json()
        if data.get("ok"):
            result = data.get("result", {})
            first = result.get("first_name") or ""
            last = result.get("last_name") or ""
            username = result.get("username") or ""
            name = f"{first} {last}".strip()
            return name or username or None
        return None
    except Exception:
        return None
    return name or None


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
      1. ``Contact.telegram_id == tgid`` and role is
         ``"assigned"`` — route through the agent loop
         using that contact's LLM credentials. The
         ``admin`` boolean (WebUI sign-in) doesn't change
         this branch.
      2. otherwise (no contact bound, OR a ``role='guest'``
         row that someone soft-auto-created via the
         fallback in step 3) — treat as GUEST and send
         the tgid discovery reply. The role gate is
         decided per-MAGI-instance (the canonical state lives
         on the row, not in a meta key).

    The legacy ``telegram.user.<tgid>.uid`` meta
    binding is **deprecated** — bindings now live on
    ``Contact.telegram_id``. The read path falls back to
    the meta for state files written before the unified
    table landed (C1.x), so a half-migrated state still
    works.
    """
    if update.effective_chat is None or update.effective_message is None:
        return
    tgid = str(update.effective_chat.id)
    display_name = (
        update.effective_chat.first_name
        or update.effective_chat.username
        or update.effective_chat.title
    )

    import os
    state_dir = os.environ.get("MAGI_STATE_DIR", STATE_DIR)

    # 1+2. Look up the bound contact. Single ORM read by
    # ``telegram_id``; the role decides what we do next.
    # Dispatch rules (per-MAGI perspective):
    #   - ``admin``    : real chat sender. The agent loop
    #                    runs; the admin's per-contact LLM
    #                    credentials (D.4+) are billed. v0
    #                    used to skip the LLM here ("admin
    #                    chat grows real admin commands in
    #                    C6+") but that left the admin
    #                    unable to use TG on mobile — fixed
    #                    so admin and assigned share the
    #                    same handler.
    #   - ``assigned`` : this MAGI serves the person. The
    #                    agent loop runs.
    #   - ``contact`` : another company contact. NOT
    #                    served by this MAGI. Cross-MAGI
    #                    access is a future concern; for
    #                    v0 we politely refuse and tell
    #                    them to talk to their own admin.
    #   - ``guest``    : not in this company at all. Same
    #                    refusal as ``contact`` so the
    #                    tgid discovery path can be
    #                    surfaced ("here's your tgid,
    #                    ask your admin to invite you").
    bound = _find_contact_by_telegram_id(state_dir, tgid)
    if bound is not None:
        contact_id, contact_role, contact_name, contact_separated, contact_admin = bound
        # After the 2024 role/admin split, ``admin`` is a
        # boolean (WebUI sign-in) rather than a role value.
        # Dispatch accepts the caller if EITHER:
        #   - ``contact_admin=True`` (a WebUI operator — any role)
        #   - ``contact_role == 'assigned'`` (the served user —
        #     the EVE they're chatting with is theirs).
        # ``role='guest'`` is NOT accepted here — a soft-
        # auto-created stranger still has to ask their
        # admin for promotion (handled in the "no contact
        # bound" path above; the auto-create we do there
        # only stamps the directory row, the chat is still
        # politely refused until the operator promotes).
        if not (contact_admin or contact_role == "assigned"):
            # ``guest`` — refuse politely without burning
            # the LLM. The hint about the tgid is the same
            # one the unknown-
            # chat path sends, so the user can pass
            # the id to whoever runs their company's
            # MAGI to get added.
            logger.info(
                "telegram: %s role not served by this MAGI; refusing",
                contact_role,
                extra={"tgid": tgid, "uid": contact_id},
            )
            await update.effective_message.reply_text(
                _replies()["unprivileged_contact"].format(
                    contact_name=contact_name, delivery_address=tgid,
                ),
            )
            return
        # ``admin`` and ``assigned`` both flow through the
        # same handler. Good — TG chat-with-EVE is a real
        # affordance for mobile operators too.
        await _handle_contact_message(
            update,
            state_dir,
            tgid,
            contact_id,
            contact_name,
            display_name,
            contact_separated,
            contact_role,
        )
        return

    # 3. No contact bound — auto-create a Contact row, then
    # politely ask the stranger to talk to their admin.
    #
    # Pre-2024 the chat just sent a tgid-discovery reply
    # without leaving a directory row. The downside was
    # the operator had no idea who was trying to reach
    # them until the stranger manually forwarded their
    # tgid. The new policy is "soft auto-create": every
    # first-time chatter lands a row in the contacts
    # table (role='guest', no provider/api_key) so the
    # operator sees them in the dashboard; the chat still
    # returns the polite tgid-discovery reply asking the
    # stranger to ask their admin to grant them
    # chat access. The operator promotes them via
    # ``PATCH /api/contacts/{id}`` with ``{"role": "assigned"}``
    # or ``{"admin": true}`` after which the next
    # inbound message from this tgid will pass the dispatch
    # gate and route through the agent loop.
    bound = _auto_create_stranger_contact(state_dir, tgid, display_name)
    if bound is not None:
        contact_id, contact_role, _, _, _, _, _ = bound
        logger.info(
            "telegram: auto-created stranger contact on first message",
            extra={
                "tgid": tgid,
                "display_name": display_name,
                "uid": contact_id,
                "role": contact_role,
            },
        )
    else:
        logger.warning(
            "telegram: auto-create stranger contact failed; "
            "still sending tgid discovery",
            extra={"tgid": tgid, "display_name": display_name},
        )
    # Either way (created or not), the chat still responds
    # with the tgid-discovery reply. The stranger gets the
    # same handoff they've always gotten; the operator
    # additionally gets a Contact row they can promote.
    await update.effective_message.reply_text(
        _replies()["unknown_sender"].format(delivery_address=tgid),
        parse_mode="HTML",
    )
    return


def _auto_create_stranger_contact(
    state_dir: str, tgid: str, display_name: str | None,
) -> tuple[int, str, str, bool, str | None, str | None] | None:
    """Create a Contact row for an unknown tgid on first message.

    Idempotent on the unique ``telegram_id`` constraint — if
    another thread / restart already inserted the row
    between the lookup and the insert, the IntegrityError
    is caught and the lookup re-runs.

    Returns the same tuple shape as
    :func:`_find_contact_by_telegram_id` (so the caller's
    fall-through dispatch is identical to the bound path).
    ``None`` on hard failure (DB locked, import error,
    IntegrityError race that we couldn't recover from).
    """
    try:
        cid_int = int(tgid)
    except (TypeError, ValueError):
        return None

    # Display-name heuristic: prefer ``display_name`` (TG
    # first_name / username / chat title); fall back to a
    # placeholder like ``stranger-<last 5 of tgid>`` so the
    # row has a non-empty name without the LLM needing to
    # fill it in immediately. The operator can rename via
    # the dashboard later.
    name = (display_name or "").strip() or f"stranger-{tgid[-5:]}"
    display_name = (display_name or "").strip() or None

    try:
        from magi.bus import bootstrap
        contacts = bootstrap(state_dir).contacts
        if contacts.find_by_telegram_id(cid_int) is None:
            contacts.create_contact(name=name, display_name=display_name, role="guest", telegram_id=cid_int, source="system")
    except Exception:
        logger.exception(
            "telegram: auto-create stranger contact failed",
            extra={"tgid": tgid},
        )
        return None

    return _find_contact_by_telegram_id(state_dir, tgid)


def _find_contact_by_telegram_id(
    state_dir: str, tgid: str
) -> tuple[int, str, str, bool, bool] | None:
    """Resolve a TG tgid to its bound contact.

    Single ORM read on ``Contact.telegram_id``; returns
    ``(uid, role, name, separated, admin)``
    on hit, ``None`` when no row has the tgid bound.
    The dispatch in :func:`_on_message` uses ``admin`` (WebUI
    sign-in bit) and ``role`` (the served-by relationship)
    independently — ``admin=True`` is the canonical
    operator flag since the 2024 split (see
    :mod:`magi.bus.models.local.contact`).

    LLM credentials come from the direct MAGIS ``magic`` table,
    not from ``contacts``. Provider resolution happens in the agent loop.

    Falls back to the legacy ``telegram.user.<tgid>.uid``
    meta key for state files written before the unified
    table landed (C1.x). The meta key is read-only here;
    bindings are now written through the
    ``PATCH /api/contacts/{id}`` endpoint.
    """
    try:
        cid_int = int(tgid)
    except (TypeError, ValueError):
        return None

    def _fields(e) -> tuple[int, str, str, bool, bool]:
        return (
            e.id,
            e.role,
            e.name,
            e.separated,
            e.admin,
        )

    try:
        from magi.bus import bootstrap
        contact = bootstrap(state_dir).contacts.find_by_telegram_id(cid_int)
        if contact is not None:
            return _fields(contact)
    except Exception:
        logger.exception(
            "telegram: ORM read failed resolving chat %s", tgid,
        )

    return None


async def _handle_contact_message(
    update: Update,
    state_dir: str,
    delivery_address: str,
    uid: int,
    contact_name: str,
    display_name: str | None,
    contact_separated: bool,
    contact_role: str,
) -> None:
    """Route a message from a bound contact into the durable agent inbox.

    LLM credentials are resolved from the runtime MAGI row in its direct
    MAGIS database rather than the Contact row — the MAGI owns the
    credentials, not the person.

    A separated contact gets a polite "you're 离职"
    reply and no LLM call.

    Session lifecycle (D.10): TG now persists chat history
    the same way WebUI does — the BUS session service writes
    one file per ``(delivery_address, session_id)`` under
    ``<workspace>/memories/sessions/<delivery_address>/<sid>.json``.
    Unlike WebUI (which has a sidebar "新对话" affordance),
    TG keeps **one session per delivery_address forever** — the
    contact never asks for a fresh thread from this side.
    The session is auto-created on the first inbound
    message and reused for every subsequent turn in that
    chat, so the file grows into the contact's complete
    history with this EVE. Per-chat / per-topic session
    splits are a future C7+ affordance.
    """
    from magi.bus import bootstrap
    from magi.bus.contracts.session import SessionMessage, new_session_id, utcnow_iso
    from magi.bus import AgentMessage

    # LLM credentials remain local to the agent. Telegram only publishes a
    # durable input and never receives provider credentials.

    if contact_separated:
        # Separated contacts can't chat with their EVE —
        # the org marked them as 离职, so the agent is
        # paused. Admin can restore via the dashboard.
        await update.effective_message.reply_text(
            _replies()["separated_contact"].format(contact_name=contact_name),
        )
        return

    text = update.effective_message.text or ""
    if not text.strip():
        # Sticker / photo / voice / etc — the agent runtime
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
    #   1. Try the *latest* session for this delivery_address
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
    store = bootstrap(state_dir).session
    session_id = _resolve_or_create_tg_session(store, delivery_address, uid)

    # Inbound append — SQLite's per-statement atomicity replaces
    # the pre-D.18 per-session ``asyncio.Lock`` that used to
    # serialise against the auto-title worker.
    #
    # D.22: ``channel="tg"`` is the cross-channel guard.
    # TG always owns the sessions it creates (we don't share
    # session_ids across channels), so in practice the check
    # never fires here — but it does if a future WebUI→TG
    # handoff ever lands. Failure is logged and treated as
    # "inbound couldn't be persisted" below; the user gets
    # a generic reply so a misrouted message doesn't crash
    # the bot.
    #
    # D.23: the first argument is now ``uid`` (the
    # session key, cross-channel), not the TG delivery_address.
    ts_in = utcnow_iso()
    try:
        post = store.append_messages(
            uid, session_id,
            [SessionMessage(
                role="user", text=text, ts=ts_in,
                message_id=new_session_id(),
            )],
            channel=Channel.TG,
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

    # -- "typing…" indicator (D.14) --------------------------------------
    # TG clears the typing state automatically when our
    # ``reply_text`` lands, but only if the LLM reply comes
    # back within ~5s — past that, the client hides the
    # indicator. So we fire an immediate ``typing`` then
    # start a 4-second refresh loop in the background;
    # cancelled after the inbound has been durably published.
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
        bootstrap(state_dir).agent_runs.publish_input(
            AgentMessage(
                # Telegram message ids are stable per chat, and the inbound
                # session message is separately persisted above. Together
                # they make webhook/update retries idempotent.
                event_id=(
                    f"telegram:{update.effective_chat.id}:"
                    f"{update.effective_message.message_id}"
                ),
                source_id=str(update.effective_message.message_id),
                # Cross-channel idempotency triple (0009_idempotency_keys):
                # TG update_id is the upstream-stable id we dedupe on.
                # Different ``event_id`` values (e.g. bot-restart re-runs
                # of the same update) collapse to the same inbox row.
                source_type="tg",
                external_event_id=str(update.update_id),
                text=text,
                channel=Channel.TG,
                session_id=session_id,
                uid=uid,
                # The bound operator's role lets the eventual agent step
                # filter privileged tools without a Telegram dependency.
                caller_role=contact_role,
            ),
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

    # DeliveryWorker appends the committed response and sends Telegram only
    # after the actor transition commits.  The inbound handler intentionally
    # returns now; a slow provider/tool must not occupy Telegram's update task.


def _resolve_or_create_tg_session(
    store,
    delivery_address: str,
    uid: int,
) -> str:
    """Return the session id to use for the next TG message.

    Policy (D.10): **one TG session per delivery address forever.**

    Look up the most recent session for ``delivery_address`` WHERE
    ``channel == 'tg'`` (not just any-channel) and reuse
    it. The earlier implementation used ``list_summaries``
    with no channel filter and re-checked the candidate's
    channel in Python — but when the latest session was a
    WebUI one (the same contact id owns sessions across
    channels since D.23), the helper would mint a fresh
    TG session every time. Result: alternating
    TG ↔ WebUI usage fragmented the TG history into N
    sessions, contradicting the D.10 promise.

    Filtering in the BUS session service (via ``find_latest_tg_session``)
    means:
      - Latest is a TG session → reuse it (the common path).
      - Latest is a WebUI session → ignored; we look at the
        most recent *TG* session instead. Only if none
        exists do we mint a fresh row.
      - No TG session at all (contact never chatted on TG,
        or the operator wiped the row) → mint fresh.

    A corrupt session file (truncated JSON) is skipped and
    triggers creation of a new one — we lose the corrupt
    thread's history but don't crash the inbound handler.
    """
    try:
        candidate_id = store.find_latest_tg_session(uid)
    except Exception:
        logger.exception(
            "telegram: session lookup failed for contact %s; minting fresh",
            uid,
        )
        candidate_id = None
    if candidate_id is not None:
        try:
            sess = store.get(uid, candidate_id)
        except Exception:
            logger.exception(
                "telegram: latest TG session %s for contact %s failed re-read; "
                "creating fresh",
                candidate_id, uid,
            )
            sess = None
        if sess is not None and sess.channel == Channel.TG:
            return candidate_id
        # ``find_latest_tg_session`` already filters, but
        # the double-check defends against a future bug
        # where someone changes the filter without updating
        # the helper. Cheap; worth the safety rail.
    # No prior TG session (or none existed) — mint a new one.
    # D.23: first arg is now uid; ``delivery_address=`` is the
    # per-channel delivery address stamped on the row's
    # ``delivery_address`` column for outbound ``send_message`` later.
    sess = store.create(
        uid, channel=Channel.TG, delivery_address=delivery_address,
    )
    return sess.session_id


# TG "typing…" action expires after ~5s, so we refresh
# every 4s while the LLM is thinking. The handler starts
# this loop in the background just before
# durable message publication and signals it to stop via the returned
# ``stop_event`` — see ``_handle_contact_message``.
_TYPING_REFRESH_SECONDS = 4.0


async def _typing_indicator_loop(
    bot,
    chat_id: int,
    stop_event: asyncio.Event,  # noqa: F821 — forward ref avoids an extra import
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
    deadline = asyncio.get_running_loop().time() + 30.0
    while not stop_event.is_set():
        remaining = deadline - asyncio.get_running_loop().time()
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
        except TimeoutError:
            # Refresh period elapsed; loop and re-send.
            continue


def start_bot(state_dir: str) -> threading.Thread | None:
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
    # Idempotent start: if a polling thread is already alive,
    # return it instead of spawning a second one.
    global _telegram_bot_thread
    with _telegram_start_lock:
        if _telegram_bot_thread is not None and _telegram_bot_thread.is_alive():
            logger.info("telegram bot already running; reusing existing thread")
            return _telegram_bot_thread

    token = bootstrap(state_dir).settings.get("telegram.bot_token")
    if not token:
        logger.info(
            "telegram: no bot token saved yet — channel idle until onboarding completes"
        )
        return None

    username = bootstrap(state_dir).settings.get("telegram.bot_username")
    # ``concurrent_updates=True`` lets a follow-up TG message
    # for the same chat enter ``_on_message`` **while** the
    # previous turn's durable run is still in flight. Without this, the
    # python-telegram-bot runtime serialises per-chat updates
    # at the dispatcher level, so a fresh user message that
    # arrives mid-tool-chain sits in the bot's queue until the
    # prior turn fully completes — D.21's interrupt poll
    # (``_drain_pending_user_messages``) never has anything
    # to drain, and the user sees "two separate batches of
    # send_message calls" instead of the new message being
    # spliced into the live loop. With concurrent updates
    # on, the new inbound is persisted to the session store
    # before the prior handler returns, so the next poll
    # picks it up, resets the iteration counter, and the
    # tool chain effectively starts fresh around the new
    # input.
    #
    # The natural serialisation point is
    # BUS ``append_messages`` — concurrent appends
    # are safe under SQLite's row-level locking (D.22 channel
    # guard + D.23 contact scoping are already enforced
    # there). The TG inbound handler remains cheap (one
    # INSERT + the async bot.send_message), and the LLM
    # call / tool chain runs to completion regardless.
    application = (
        Application.builder()
        .token(token)
        .concurrent_updates(True)
        .connect_timeout(15)
        .read_timeout(15)
        .write_timeout(15)
        .pool_timeout(5)
        .build()
    )
    application.add_handler(MessageHandler(filters.ALL, _on_message))

    async def _run_forever() -> None:
        global _telegram_app, _telegram_shutdown_event
        _telegram_shutdown_event = asyncio.Event()
        try:
            await application.initialize()
        except RuntimeError as e:
            logger.warning(
                "telegram _run_forever: initialize() raised %s — "
                "another worker likely already owns the Updater",
                e,
            )
            return
        _telegram_app = application
        set_telegram_bot(application.bot)
        try:
            await application.start()
            await application.updater.start_polling(
                poll_interval=1.0,
                timeout=10,
            )
            # Park until shutdown is requested (via stop_bot)
            # or the process exits (daemon thread killed).
            await _telegram_shutdown_event.wait()
        except RuntimeError as e:
            # ``Updater already running`` again — the previous
            # worker's Updater was still tied to its asyncio
            # loop when this fresh worker tried to grab it.
            # Same situation as the ``initialize()`` guard
            # above: log and exit cleanly.
            logger.warning(
                "telegram _run_forever: start_polling raised %s — "
                "another worker likely already owns the Updater",
                e,
            )
            return
        finally:
            clear_telegram_bot()

    def _thread_target() -> None:
        asyncio.run(_run_forever())

    thread = threading.Thread(
        target=_thread_target,
        name="telegram-bot",
        daemon=True,
    )
    with _telegram_start_lock:
        _telegram_bot_thread = thread
    thread.start()
    logger.info(
        "telegram bot started",
        extra={"username": username, "state_dir": state_dir},
    )
    return thread


def stop_bot() -> None:
    """Gracefully stop the Telegram bot polling loop.

    Idempotent — safe to call when no bot is running.
    The daemon thread exits once the asyncio loop unparks
    from the shutdown event.
    """
    global _telegram_bot_thread, _telegram_app, _telegram_shutdown_event
    with _telegram_start_lock:
        if _telegram_shutdown_event is not None:
            _telegram_shutdown_event.set()
        if _telegram_bot_thread is not None and _telegram_bot_thread.is_alive():
            _telegram_bot_thread.join(timeout=5)
        _telegram_bot_thread = None
        _telegram_app = None
        _telegram_shutdown_event = None
        clear_telegram_bot()
    logger.info("telegram bot stopped")


def is_running() -> bool:
    """Return True when the TG bot polling thread is alive."""
    return _telegram_bot_thread is not None and _telegram_bot_thread.is_alive()
