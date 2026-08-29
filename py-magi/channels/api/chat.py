"""ADAM's chat endpoint — the WebUI channel's "send a
message to the LLM" route.

The frontend POSTs text into the private durable bus, then
waits for the corresponding agent run. The request/response
shape remains compatible while the agent owns sequential
consumption rather than the HTTP handler calling a loop.

LLM credentials
===============

Credentials are resolved inside :func:`providers.factory
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

from fastapi import APIRouter, Request, status
from pydantic import BaseModel, Field

from old_bus import Bus
from old_bus.firmwares.jobs.chatNotifyJob import ChatNotifyJob
from old_bus.firmwares.books.local.conversationBook import ChannelMismatchError
from channels.api.auth_gates import AdminGate
from channels.api.chat_conversations import ConversationMessageOut
from channels.api.dependencies import BusDep
from channels.api.errors import MagiHTTPException

logger = logging.getLogger("magi.api.chat")

router = APIRouter(tags=["chat"])


# Tuned for the common case (a chat turn reply is well under
# 4K chars). If the model genuinely needs more for some
# specific task, raise this — the audit row already records
# the truncation so the operator can see it happened.
_MAX_INPUT_CHARS = 8000
_MAX_OUTPUT_CHARS = 4000


def _resolve_caller_credentials(bus: Bus, contact_id: int) -> int:
    """Look up the operator's Contact row by their
    ``contact_id`` (the cookie value post-D.24) and return the
    resolved ``contact_id``.

    LLM credentials live on the MAGI's local ``settings_book``
    (provider + key), not on ``contacts`` — the agent worker
    reads them internally through :func:`providers.factory
    .get_provider`. Token-usage recording is still per-
    Contact (``token_usage.contact_id``).

    The operator's ``role`` is **not** returned here — the agent
    worker resolves it from :meth:`ContactBook.get` at claim time
    (a live value, not a publish-time snapshot).

    Raises ``MagiHTTPException``:

      - ``401 chat.unknown_sender`` if the contact id
        doesn't resolve to a row.
    """
    contact = bus.contacts_book.get(contact_id)

    if contact is None:
        raise MagiHTTPException(
            status_code=401,
            code="chat.unknown_sender",
            detail="no Contact row bound to this cookie",
        )

    return contact.id


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
    conversation_id: int | None = None


class ChatSendResponse(BaseModel):
    job_id: int
    status: str = "accepted"
    # Always returned so the frontend can stash it on a
    # fresh chat. For an existing-conversation send it equals
    # what was sent in.
    conversation_id: int
    messages: list[ConversationMessageOut] = []


class ChatNotifyStatusResponse(BaseModel):
    """Channel-facing receipt state for one submitted ChatNotifyJob."""

    job_id: int
    status: str


@router.post("/chat/send", response_model=ChatSendResponse, status_code=status.HTTP_202_ACCEPTED)
async def send_chat(
    payload: ChatSendRequest,
    request: Request,
    _admin: AdminGate,
    bus: BusDep,
) -> ChatSendResponse:
    """Persist input and return a run handle without waiting for inference.

    The LLM is selected from the operator's Contact row
    (``provider`` + ``api_key`` configured from Settings). If those fields
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

    # D.24: identity resolution mirrors chat_conversations. Two legs:
    # (1) signed WebUI proxy envelope → trust the HMAC, use the
    #     operator identity the proxy asserted; (2) ``magi_session``
    #     cookie → fallback for direct-runtime callers (legacy
    #     internal callers that haven't gone through the WebUI proxy).
    # ``_resolve_caller_credentials`` re-checks the row exists and
    # surfaces the operator's role for the agent-loop tool menu filter.
    # LLM credentials are resolved inside the actor step via the
    # factory. The cookie / proxy id is the cross-channel identity;
    # the per-channel delivery address (TG chat id) is looked up
    # separately by the channel dispatcher (D.28) below — WebUI
    # doesn't need it for send / read but we stamp it on the
    # conversation row for cross-channel tooling.
    from channels.api.auth import resolve_session
    from channels.api.auth_gates import _proxy_identity

    proxy = _proxy_identity(request)
    if proxy is not None:
        cookie_contact_id = int(proxy[0])
    else:
        cookie_raw = request.cookies.get("magi_session", "")
        session = resolve_session(bus, cookie_raw)
        if session is None:
            raise MagiHTTPException(
                status_code=401,
                code="chat.unknown_sender",
                detail="no signed-in contact",
            )
        cookie_contact_id = int(session["contact_id"])
    contact_id = _resolve_caller_credentials(bus, cookie_contact_id)
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
        # D.23: conversation key is now ``contact_id`` (the
        # cross-channel identity of the operator),
        # not the cookie's chat id. The chat id is
        # still carried on the row's
        # ``delivery_address`` column for
        # legacy / outbound-delivery reasons, but it
        # is NOT a conversation key. ``get_for_owner`` returns
        # ``None`` for a missing / foreign row — no id-format
        # validation in the ORM layer.
        existing = store.get_for_owner(contact_id=contact_id, conversation_id=conversation_id)
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
        contact = bus.contacts_book.get(contact_id)
        tg_im_id = str(contact.tgid) if contact and contact.tgid is not None else ""
        # ``conversations_book.add`` owns ``conversation_id`` itself —
        # callers never pass it (see :meth:`ConversationBook.add`
        # docstring). The new id comes back on the returned
        # ``Conversation`` below.
        from old_bus.firmwares.books.local.conversationBook import Conversation

        sess = Conversation(
            contact_id=contact_id,
            channel="webui",
            delivery_address=tg_im_id,
        )
        # ``BaseBook.add`` returns the database-generated id; it does not
        # mutate the immutable DTO passed to it.  Carry the returned id into
        # the turn job (and consequently the transcript/delivery jobs).
        # Reading ``sess.id`` here leaves it at its construction default (0),
        # which makes the message FK write and WebUI delivery both fail.
        conversation_id = store.add(sess)

    # D.22 cross-channel guard + chat_messages write + chatNotifyJob
    # enqueue are all consolidated inside
    # :meth:`chatNotifyBoard.publish`. The user message is
    # persisted to ``chat_messages`` at the same chokepoint, so
    # the channel never touches ``messages_book`` directly.
    try:
        job_id = bus.agent_job_board.publish(
            ChatNotifyJob(
                text=text,
                channel="webui",
                contact_id=contact_id,
                conversation_id=conversation_id,
            )
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
            "chat: failed to publish chatNotifyJob for conversation %s",
            conversation_id,
        )
        raise MagiHTTPException(  # noqa: B904
            status_code=500,
            code="chat.conversation_store_failed",
            detail="could not publish chat turn",
        )

    return ChatSendResponse(job_id=job_id, conversation_id=conversation_id)


@router.get("/chat/notifications/{job_id}", response_model=ChatNotifyStatusResponse)
def get_chat_notification_status(
    job_id: int,
    _admin: AdminGate,
    bus: BusDep,
) -> ChatNotifyStatusResponse:
    """Read the durable channel→Agent receipt state for this caller's turn."""
    job_status = bus.agent_job_board.check_job_status(job_id=job_id)
    if job_status is None:
        raise MagiHTTPException(
            status_code=404,
            code="not_found.chat_notification",
            detail=f"chat notification {job_id} not found",
        )
    return ChatNotifyStatusResponse(job_id=job_id, status=job_status.value)
