"""Adam's chat endpoint — the WebUI channel's "send a
message to the LLM" route.

The frontend POSTs text into the private durable bus, then
waits for the corresponding agent run. The request/response
shape remains compatible while the agent owns sequential
consumption rather than the HTTP handler calling a loop.

LLM credentials
===============

Credentials are resolved inside :func:`magi.agent.llm.factory
.get_provider` — the chat handler doesn't take them as
parameters. The seeded adam ``Magi`` row owns the
provider + API key; the chat handler only reads the
operator's ``role`` (for the tool-menu filter) and ``uid``
(for the session). Token usage is still recorded per-
operator via ``token_usage.uid``.

The cookie / uid / row-exists checks are NOT done here
because the auth gate (``AdminGate``) has already done them
and returned 401. If the gate let the request through, the
admin row exists.

Anti-abuse: the request body is bounded (max 8K text) and
the reply is bounded (max 4K text, same as TG). The LLM
has its own ``max_tokens`` cap; the 4K byte cap is a
defensive ceiling on top.
"""

from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from magi.channels.webui.api.auth_gates import AdminGate
from magi.channels.webui.api.errors import MagiHTTPException
from magi.channels.webui.api.chat_sessions import SessionMessageOut
from magi.channels import Channel
from magi.agent.worker import (
    AgentRunFailed,
    AgentRunTimedOut,
    submit_and_wait_agent_message,
)
from magi.bus import AgentMessage
from magi.agent.memory.session import (
    ChannelMismatchError,
    SessionMessage,
    SessionPathError,
    SessionStore,
    new_session_id,
    utcnow_iso as _utcnow_iso,
)
from magi.db import Contact, open_session, require_state_dir

logger = logging.getLogger("magi.api.chat")

router = APIRouter(tags=["chat"])


# Tuned for the common case (a chat turn reply is well under
# 4K chars). If the model genuinely needs more for some
# specific task, raise this — the audit row already records
# the truncation so the operator can see it happened.
_MAX_INPUT_CHARS = 8000
_MAX_OUTPUT_CHARS = 4000


def _state_dir() -> str:
    return require_state_dir()


def _resolve_caller_credentials(
    state_dir: str, uid: int
) -> tuple[int, str]:
    """Look up the operator's Contact row by their
    ``uid`` (the cookie value post-D.24) and return
    ``(uid, role)``.

    LLM credentials live on ``magic`` (the Adam MAGIC
    owns the provider + key), not on ``contacts`` — and
    the chat handler doesn't carry them anymore.
    the agent worker reads them
    internally through :func:`magi.agent.llm.factory
    .get_provider`. Token-usage recording is still per-
    Contact (``token_usage.uid``).

    The ``role`` field is included so the chat handler
    can put it on the durable agent message
    as ``caller_role`` — ``schedule_task`` and the
    action-item trio are gated to ``admin`` and ``assigned``
    only, and the agent worker needs to strip them out of
    other roles' tool menus.

    Raises ``MagiHTTPException``:

      - ``401 chat.unknown_sender`` if the contact id
        doesn't resolve to a row.
    """
    try:
        with open_session() as session:
            contact = session.get(Contact, uid)
    except Exception:
        logger.exception(
            "chat: ORM lookup failed for uid %s", uid,
        )
        raise MagiHTTPException(
            status_code=500,
            code="chat.lookup_failed",
            detail="could not load operator's Contact record",
        )

    if contact is None:
        raise MagiHTTPException(
            status_code=401,
            code="chat.unknown_sender",
            detail="no Contact row bound to this cookie",
        )

    return contact.id, contact.role


class ChatSendRequest(BaseModel):
    """Body for ``POST /api/chat/send``.

    ``text`` is the only required field. ``session_id``
    (optional) ties the message to a persisted session;
    the cookie's uid pins the session to that operator.
    If absent, the backend auto-creates a new session
    and returns its id in the response — so the frontend
    doesn't have to know about session lifecycle.
    """

    text: str = Field(min_length=1, max_length=_MAX_INPUT_CHARS)
    # Upper-bounded 64 chars to bound validation work on
    # the server side. 64 is comfortably above the
    # Crockford base32-ULID length (26) so any plausible
    # future id format is accommodated. A hand-crafted
    # value outside this length is treated as
    # ``validation.session_id_invalid``.
    session_id: str | None = Field(default=None, max_length=64)


class ChatSendResponse(BaseModel):
    reply: str
    # Always returned so the frontend can stash it on a
    # fresh chat. For an existing-session send it equals
    # what was sent in.
    session_id: str
    messages: list[SessionMessageOut] = []


@router.post("/chat/send", response_model=ChatSendResponse)
async def send_chat(
    payload: ChatSendRequest,
    request: Request,
    _admin: AdminGate,
) -> ChatSendResponse:
    """Send ``text`` to the LLM and return the reply.

    The LLM is selected from the operator's Contact row
    (``provider`` + ``api_key`` set during onboarding or
    later via the contact detail panel). If those fields
    are empty the request is rejected with
    ``403 chat.llm_credentials_required`` — no silent
    fall-back to the system default. The audit row records
    the operator's ``uid`` regardless.

    Session lifecycle (D.6):
      - The user message is appended to the resolved
        session **before** the LLM call so a crash mid-call
        leaves the inbound row visible in the file. The
        LLM reply is appended after the call returns.
      - The assistant message is appended **after** the LLM
        returns successfully (matches ``chat.outbound``).
      - If no ``session_id`` is sent, a new session is
        created on-the-fly; the id is returned in the
        response so the frontend can persist it.
      - If the supplied ``session_id`` is invalid or has
        been deleted, the same auto-create path runs.
    """
    text = payload.text.strip()
    if not text:
        raise MagiHTTPException(
            status_code=400,
            code="validation.text_required",
            detail="text must not be empty",
        )

    # D.24: the cookie's value IS the uid. The
    # auth gate already proved it's a live admin session;
    # ``_resolve_caller_credentials`` re-checks the row
    # exists and surfaces the operator's role for the
    # agent-loop tool menu filter. LLM credentials are
    # resolved inside :func:`handle_message` via the
    # factory. The cookie is the cross-channel identity;
    # the per-channel delivery address (TG chat id) is
    # looked up separately by the channel dispatcher
    # (D.28) below — WebUI doesn't need it for send / read
    # but we stamp it on the session row for cross-channel
    # tooling.
    from magi.channels.webui.api.auth import _verify_signed_uid
    cookie_raw = request.cookies.get("magi_session", "")
    cookie_uid = _verify_signed_uid(cookie_raw)
    if cookie_uid is None:
        raise MagiHTTPException(
            status_code=401,
            code="chat.unknown_sender",
            detail="no signed-in contact",
        )
    uid, contact_role = _resolve_caller_credentials(
        _state_dir(), cookie_uid
    )
    # D.24: per-channel delivery address stamped on the
    # session row's ``delivery_address`` column (renamed
    # from the legacy per-channel chat-id column in D.28).
    # WebUI
    # doesn't need it for send/read, but cross-channel
    # tooling may address the operator's bot from this
    # column. ``""`` if the operator never bound TG.

    # -- session lifecycle ------------------------------------------
    # ``uid`` (cross-channel identity) is the session key
    # — NOT the per-channel delivery address. The store
    # resolves rows by uid; the channel adapter interprets
    # the delivery address when it has to push a reply.
    store = SessionStore(_state_dir())
    session_id = payload.session_id
    # The per-channel delivery address stamped on the
    # session row. ``""`` if the operator never bound TG.
    # We always need this — either from the existing row
    # (when the caller passed a session_id) or by reading
    # the Contact row via the channel dispatcher (when we
    # mint a fresh session below).
    delivery_address = ""
    if session_id:
        try:
            # D.23: session key is now ``uid`` (the
            # cross-channel identity of the operator),
            # not the cookie's chat id. The chat id is
            # still carried on the row's
            # ``delivery_address`` column for
            # legacy / outbound-delivery reasons, but it
            # is NOT a session key.
            existing = store.get(uid, session_id)
        except SessionPathError as e:
            raise MagiHTTPException(
                status_code=400,
                code="validation.session_id_invalid",
                detail=str(e),
            )
        # Stale / deleted / never-existed → auto-create
        # fresh. Keeps the operator unblocked if they
        # re-open a tab after a manual delete.
        if existing is None:
            session_id = None
        else:
            # Carry the row's delivery address forward to
            # the auto-title job below (which runs on
            # every fresh session). Reading the column
            # here keeps the dispatcher lookup scoped to
            # the auto-create branch — when the row
            # already exists, we trust its own column.
            delivery_address = existing.delivery_address or ""
    if not session_id:
        # ``delivery_address=`` here is the per-channel
        # delivery address stamped on the session row.
        # The value comes from the channel dispatcher
        # (D.28 centralised the uid → IM-id mapping in
        # the adapter registry, so this file no longer
        # reads ``Contact.telegram_id`` directly). An
        # empty string when the operator has no TG
        # binding (still legal — WebUI rows don't push
        # anywhere).
        from magi.channels import dispatcher as channel_dispatcher
        tg_im_id = channel_dispatcher.lookup_im_id(uid, Channel.TG) or ""
        sess = store.create(
            uid, channel=Channel.WEBUI, delivery_address=tg_im_id,
        )
        session_id = sess.session_id

    # Inbound audit + SQLite append happen atomically inside
    # ``store.append_messages`` (single INSERT). Pre-D.18 this
    # block held the per-session ``asyncio.Lock`` so the
    # auto-title worker (D.7) saw a coherent state; SQLite's
    # per-statement atomicity replaces that need.
    #
    # D.22: ``channel=Channel.WEBUI`` is the cross-channel guard —
    # if the session was created on TG, the store raises
    # ``ChannelMismatchError`` and we 403 the caller instead
    # of mixing two LLM loops into one history.
    ts_in = _utcnow_iso()
    inbound_message_id = new_session_id()
    try:
        post = store.append_messages(
            uid, session_id,
            [SessionMessage(
                role="user", text=text, ts=ts_in,
                message_id=inbound_message_id,
            )],
            channel=Channel.WEBUI,
        )
    except ChannelMismatchError as e:
        # D.22: the session was created on a different
        # channel (most commonly TG). Refuse to write so
        # two LLM loops don't fight over the same history.
        # The UI surfaces this as a banner next to the
        # message input; the user can continue the
        # conversation on the original channel.
        logger.info(
            "chat: refusing cross-channel write "
            "(session=%s owned by %r, caller=webui)",
            session_id, e.session_channel,
        )
        raise MagiHTTPException(
            status_code=403,
            code="chat.session_channel_mismatch",
            detail=(
                f"this session was started on "
                f"{e.session_channel!r}; continue the "
                "conversation on that channel."
            ),
        )
    except Exception:
        logger.exception(
            "chat: failed to append user message for session %s", session_id,
        )
        raise MagiHTTPException(
            status_code=500,
            code="chat.session_store_failed",
            detail="could not persist chat message",
        )

    # D.7: fire the auto-title job once per session — when
    # ``post.messages`` is exactly the user message we just
    # appended (so this is the inaugural user message of a
    # fresh session). Subsequent user messages
    # (``len(messages) >= 3`` — user, assistant, user) don't
    # re-enqueue. ``enqueue_title_job`` is fire-and-forget;
    # no slow work happens on the request path here.
    # LLM credentials are resolved inside the worker.
    if len(post.messages) == 1:
        from magi.agent.memory.session.auto_title import enqueue_title_job
        await enqueue_title_job(
            delivery_address=delivery_address,
            session_id=session_id,
            uid=uid,
        )

    try:
        reply = await submit_and_wait_agent_message(
            AgentMessage(
                # The persisted inbound session-message id is the producer's
                # idempotency key. A network retry cannot create a second
                # agent turn for that exact input.
                event_id=f"webui:{session_id}:{inbound_message_id}",
                source_id=inbound_message_id,
                text=text,
                channel=Channel.WEBUI,
                session_id=session_id,
                uid=uid,
                caller_role=contact_role,
            ),
            state_dir=_state_dir(),
        )
    except AgentRunFailed as e:
        if e.result.error_code != "magi.llm_credentials_required":
            logger.exception(
                "chat: agent run failed for session=%s uid=%s", session_id, uid,
            )
            raise MagiHTTPException(
                status_code=500,
                code=e.result.error_code or "chat.agent_crashed",
                detail=(
                    f"agent run failed: {e} — try again. "
                    "If the problem persists, check server logs."
                ),
            )
        # The MAGI runtime has no provider / API key
        # configured. Surface this as 503 (system-side
        # misconfiguration) so the frontend can show a
        # "configure in 智能体管理" banner.
        logger.warning(
            "chat: MAGI has no LLM credentials: %s", e,
        )
        raise MagiHTTPException(
            status_code=503,
            code="magi.llm_credentials_required",
            detail=(
                "MAGI runtime has no LLM provider / API key "
                "configured; set them via PATCH "
                "/api/magic/{adam_id}"
            ),
        )
    except AgentRunTimedOut as e:
        raise MagiHTTPException(
            status_code=504,
            code="chat.agent_timeout",
            detail=(
                f"agent run timed out: {e} — it may still finish in the background."
            ),
        )

    # Defensive truncation — the agent worker should already
    # cap via the LLM's max_tokens, but a misbehaving model
    # could still send a multi-megabyte response. We trim
    # here so the WebUI doesn't choke rendering a 5MB
    # string.
    if len(reply) > _MAX_OUTPUT_CHARS:
        reply = reply[: _MAX_OUTPUT_CHARS - 20] + "\n\n…(回复过长，已截断)"

    # Outbound audit-aligned append. A failure here is
    # logged but does NOT raise — the operator already
    # got the reply and a missing history line is worse
    # than a console line. The same ``channel=Channel.WEBUI``
    # guard applies (D.22); a mismatch here would mean
    # the worker somehow ran against a TG-owned
    # session, which the inbound check above already
    # blocked. Belt and braces.
    ts_out = _utcnow_iso()
    try:
        store.append_messages(
            uid, session_id,
            [SessionMessage(
                role="assistant", text=reply, ts=ts_out,
                message_id=new_session_id(),
            )],
            channel=Channel.WEBUI,
        )
    except Exception:
        logger.exception(
            "chat: failed to append assistant message for session %s",
            session_id,
        )

    # Load the full session so the frontend sees messages
    # appended by tools (e.g. ``send_message``) in the same
    # turn. Without this, tool-side messages land in the DB
    # but never render until the next page refresh.
    #
    # This is intentionally best-effort — if the session
    # store hiccups during the read-back, we still return the
    # reply. A missing ``messages`` list on the response is
    # a minor glitch (frontend falls back to its own
    # scroll), whereas a 500 swallows everything.
    messages: list[SessionMessageOut] = []
    try:
        post = store.get(uid, session_id)
        if post is not None:
            messages = [
                SessionMessageOut(
                    message_id=m.message_id,
                    role=m.role,
                    ts=m.ts,
                    text=m.text,
                )
                for m in post.messages
            ]
    except Exception:
        logger.exception(
            "chat: failed to load session %s for response; "
            "reply was delivered, messages list omitted",
            session_id,
        )

    return ChatSendResponse(
        reply=reply, session_id=session_id, messages=messages,
    )
