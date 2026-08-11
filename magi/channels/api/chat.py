"""ADAM's chat endpoint — the WebUI channel's "send a
message to the LLM" route.

The frontend POSTs text into the private durable bus, then
waits for the corresponding agent run. The request/response
shape remains compatible while the agent owns sequential
consumption rather than the HTTP handler calling a loop.

LLM credentials
===============

Credentials are resolved inside :func:`magi.providers.factory
.get_provider` — the chat handler doesn't take them as
parameters. The seeded adam ``Magi`` row owns the
provider + API key; the chat handler only reads the
operator's ``role`` (for the tool-menu filter) and ``contact_id``
(for the conversation). Token usage is still recorded per-
operator via ``token_usage.contact_id``.

The cookie / contact_id / row-exists checks are NOT done here
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
from datetime import UTC

from fastapi import APIRouter, Request, status
from pydantic import BaseModel, Field

from magi.bus import Bus
from magi.bus.library.local.conversationBook import (
    ChannelMismatchError,
    ConversationMessage,
    ConversationPathError,
)
from magi.channels import Channel
from magi.channels.api.auth_gates import AdminGate
from magi.channels.api.chat_conversations import ConversationMessageOut
from magi.channels.api.dependencies import BusDep
from magi.channels.api.errors import MagiHTTPException

logger = logging.getLogger("magi.api.chat")

router = APIRouter(tags=["chat"])


# Tuned for the common case (a chat turn reply is well under
# 4K chars). If the model genuinely needs more for some
# specific task, raise this — the audit row already records
# the truncation so the operator can see it happened.
_MAX_INPUT_CHARS = 8000
_MAX_OUTPUT_CHARS = 4000


def _utcnow_iso() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    from datetime import datetime

    return datetime.now(UTC).isoformat()


def new_conversation_id() -> str:
    """Generate a new conversation/message id as a Crockford-base32
    ULID-like string (26 chars)."""
    import uuid

    raw = uuid.uuid4().bytes  # 16 bytes = 128 bits
    # Encode as 26-char Crockford-like base32 string:
    # 16 bytes → ~26 chars in base32 (ceil(128/5) = 26).
    alphabet = "0123456789abcdefghjkmnpqrstvwxyz"
    bits = int.from_bytes(raw, "big")
    chars = []
    for _ in range(26):
        chars.append(alphabet[bits & 31])
        bits >>= 5
    return "".join(reversed(chars))


def _resolve_caller_credentials(bus: Bus, contact_id: int) -> tuple[int, str]:
    """Look up the operator's Contact row by their
    ``contact_id`` (the cookie value post-D.24) and return
    ``(contact_id, role)``.

    LLM credentials live on the MAGI's local ``settings_book``
    (provider + key), not on ``contacts`` — and
    the chat handler doesn't carry them anymore.
    the agent worker reads them
    internally through :func:`magi.providers.factory
    .get_provider`. Token-usage recording is still per-
    Contact (``token_usage.contact_id``).

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
    contact = bus.contacts_book.get(contact_id=contact_id)

    if contact is None:
        raise MagiHTTPException(
            status_code=401,
            code="chat.unknown_sender",
            detail="no Contact row bound to this cookie",
        )

    return contact.id, contact.role or "guest"


class ChatSendRequest(BaseModel):
    """Body for ``POST /api/chat/send``.

    ``text`` is the only required field. ``conversation_id``
    (optional) ties the message to a persisted conversation;
    the cookie's contact_id pins the conversation to that operator.
    If absent, the backend auto-creates a new conversation
    and returns its id in the response — so the frontend
    doesn't have to know about conversation lifecycle.
    """

    text: str = Field(min_length=1, max_length=_MAX_INPUT_CHARS)
    # Upper-bounded 64 chars to bound validation work on
    # the server side. 64 is comfortably above the
    # Crockford base32-ULID length (26) so any plausible
    # future id format is accommodated. A hand-crafted
    # value outside this length is treated as
    # ``validation.conversation_id_invalid``.
    conversation_id: str | None = Field(default=None, max_length=64)


class ChatSendResponse(BaseModel):
    job_id: str
    status: str = "accepted"
    # Always returned so the frontend can stash it on a
    # fresh chat. For an existing-conversation send it equals
    # what was sent in.
    conversation_id: str
    messages: list[ConversationMessageOut] = []


@router.post("/chat/send", response_model=ChatSendResponse, status_code=status.HTTP_202_ACCEPTED)
async def send_chat(
    payload: ChatSendRequest,
    request: Request,
    _admin: AdminGate,
    bus: BusDep,
) -> ChatSendResponse:
    """Persist input and return a run handle without waiting for inference.

    The LLM is selected from the operator's Contact row
    (``provider`` + ``api_key`` set during onboarding or
    later via the contact detail panel). If those fields
    are empty the request is rejected with
    ``403 chat.llm_credentials_required`` — no silent
    fall-back to the system default. The audit row records
    the operator's ``contact_id`` regardless.

    Conversation lifecycle (D.6):
      - The user message is appended to the resolved
        conversation **before** the LLM call so a crash mid-call
        leaves the inbound row visible in the file. The
        LLM reply is appended after the call returns.
      - The assistant message is appended **after** the LLM
        returns successfully (matches ``chat.outbound``).
      - If no ``conversation_id`` is sent, a new conversation is
        created on-the-fly; the id is returned in the
        response so the frontend can persist it.
      - If the supplied ``conversation_id`` is invalid or has
        been deleted, the same auto-create path runs.
    """
    text = payload.text.strip()
    if not text:
        raise MagiHTTPException(
            status_code=400,
            code="validation.text_required",
            detail="text must not be empty",
        )

    # D.24: the cookie's value IS the contact_id. The
    # auth gate already proved it's a live admin operator;
    # ``_resolve_caller_credentials`` re-checks the row
    # exists and surfaces the operator's role for the
    # agent-loop tool menu filter. LLM credentials are
    # resolved inside the actor step via the
    # factory. The cookie is the cross-channel identity;
    # the per-channel delivery address (TG chat id) is
    # looked up separately by the channel dispatcher
    # (D.28) below — WebUI doesn't need it for send / read
    # but we stamp it on the conversation row for cross-channel
    # tooling.
    from magi.channels.api.auth import resolve_session

    cookie_raw = request.cookies.get("magi_session", "")
    session = resolve_session(bus, cookie_raw)
    if session is None:
        raise MagiHTTPException(
            status_code=401,
            code="chat.unknown_sender",
            detail="no signed-in contact",
        )
    cookie_contact_id = int(session["contact_id"])
    contact_id, contact_role = _resolve_caller_credentials(bus, cookie_contact_id)
    # D.24: per-channel delivery address stamped on the
    # conversation row's ``delivery_address`` column (renamed
    # from the legacy per-channel chat-id column in D.28).
    # WebUI
    # doesn't need it for send/read, but cross-channel
    # tooling may address the operator's bot from this
    # column. ``""`` if the operator never bound TG.

    # -- conversation lifecycle -----------------------------------------
    # ``contact_id`` (cross-channel identity) is the conversation key
    # — NOT the per-channel delivery address. The store
    # resolves rows by contact_id; the channel adapter interprets
    # the delivery address when it has to push a reply.
    store = bus.conversations_book
    conversation_id = payload.conversation_id
    # The per-channel delivery address stamped on the
    # conversation row. ``""`` if the operator never bound TG.
    # We always need this — either from the existing row
    # (when the caller passed a conversation_id) or by reading
    # the Contact row via the channel dispatcher (when we
    # mint a fresh conversation below).
    if conversation_id:
        try:
            # D.23: conversation key is now ``contact_id`` (the
            # cross-channel identity of the operator),
            # not the cookie's chat id. The chat id is
            # still carried on the row's
            # ``delivery_address`` column for
            # legacy / outbound-delivery reasons, but it
            # is NOT a conversation key.
            existing = store.get_for_owner(contact_id=contact_id, conversation_id=conversation_id)
        except ConversationPathError as e:
            raise MagiHTTPException(  # noqa: B904
                status_code=400,
                code="validation.conversation_id_invalid",
                detail=str(e),
            )
        # Stale / deleted / never-existed → auto-create
        # fresh. Keeps the operator unblocked if they
        # re-open a tab after a manual delete.
        if existing is None:
            conversation_id = None
        else:
            # Carry the row's delivery address forward to
            # the auto-title job below (which runs on
            # every fresh conversation). Reading the column
            # here keeps the dispatcher lookup scoped to
            # the auto-create branch — when the row
            # already exists, we trust its own column.
            pass
    if not conversation_id:
        # ``delivery_address=`` here is the per-channel
        # delivery address stamped on the conversation row.
        # The value comes from the channel dispatcher
        # (D.28 centralised the contact_id → IM-id mapping in
        # the adapter registry, so this file no longer
        # reads ``Contact.tgid`` directly). An
        # empty string when the operator has no TG
        # binding (still legal — WebUI rows don't push
        # anywhere).
        contact = bus.contacts_book.get(contact_id=contact_id)
        tg_im_id = str(contact.tgid) if contact and contact.tgid is not None else ""
        sess = store.add(
            conversation_id=new_conversation_id(),
            contact_id=contact_id,
            channel=Channel.WEBUI,
            delivery_address=tg_im_id,
        )
        conversation_id = sess.conversation_id

    # Inbound audit + SQLite append happen atomically inside
    # ``store.append_messages`` (single INSERT). Pre-D.18 this
    # block held the per-conversation ``asyncio.Lock`` so the
    # auto-title worker (D.7) saw a coherent state; SQLite's
    # per-statement atomicity replaces that need.
    #
    # D.22: ``channel=Channel.WEBUI`` is the cross-channel guard —
    # if the conversation was created on TG, the store raises
    # ``ChannelMismatchError`` and we 403 the caller instead
    # of mixing two LLM loops into one history.
    ts_in = _utcnow_iso()
    inbound_message_id = new_conversation_id()
    try:
        store.append_messages(
            contact_id,
            conversation_id,
            [
                ConversationMessage(
                    role="user",
                    text=text,
                    ts=ts_in,
                    message_id=inbound_message_id,
                )
            ],
            channel=Channel.WEBUI,
        )
    except ChannelMismatchError as e:
        # D.22: the conversation was created on a different
        # channel (most commonly TG). Refuse to write so
        # two LLM loops don't fight over the same history.
        # The UI surfaces this as a banner next to the
        # message input; the user can continue the
        # conversation on the original channel.
        logger.info(
            "chat: refusing cross-channel write (conversation=%s owned by %r, caller=webui)",
            conversation_id,
            e.conversation_channel,
        )
        raise MagiHTTPException(  # noqa: B904
            status_code=403,
            code="chat.conversation_channel_mismatch",
            detail=(
                f"this conversation was started on "
                f"{e.conversation_channel!r}; continue the "
                "conversation on that channel."
            ),
        )
    except Exception:
        logger.exception(
            "chat: failed to append user message for conversation %s",
            conversation_id,
        )
        raise MagiHTTPException(  # noqa: B904
            status_code=500,
            code="chat.conversation_store_failed",
            detail="could not persist chat message",
        )

    from magi.bus.guild.chatJob import publish_chat

    # Stable producer-side idempotency: the inbound message
    # id is what makes a network retry collapse to the same inbox row.
    chat_job_id = f"webui:{conversation_id}:{inbound_message_id}"
    job_id = publish_chat(
        bus,
        text=text,
        channel=Channel.WEBUI,
        contact_id=contact_id,
        conversation_id=conversation_id,
        caller_role=contact_role,
        job_id=chat_job_id,
        correlation_id=inbound_message_id,
    )
    return ChatSendResponse(job_id=job_id, conversation_id=conversation_id)
