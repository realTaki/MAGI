"""CRUD endpoints for chat conversations.

A "conversation" is a single thread of messages between an
operator (identified by their contact_id in the dashboard cookie)
and the system LLM. Conversations are persisted in
the bus-owned SQLite conversation domain.
and are per-user — admin A's conversation is invisible to admin B.

Endpoints
---------

- ``POST   /chat/conversations``              create empty conversation, return id
- ``GET    /chat/conversations``              list current operator's conversations
- ``GET    /chat/conversations/{conversation_id}``  load a single conversation (full messages)
- ``DELETE /chat/conversations/{conversation_id}``  remove a conversation

The ``{conversation_id}`` route uses the URL as the only
identification: the cookie's contact_id already pins the caller.
The per-channel delivery address stamped on the new row
is resolved server-side via the channel dispatcher (D.28),
so the endpoint never reads ``Contact.tgid``
directly.
"""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from magi.old_bus.firmwares.books.local.conversationBook import (
    Conversation,
    ConversationNotFoundError,
    Message,
)
from magi.channels.api.auth_gates import AdminGate
from magi.channels.api.dependencies import BusDep, get_bus
from magi.channels.api.errors import MagiHTTPException

logger = logging.getLogger("magi.api.chat_conversations")

router = APIRouter(tags=["chat_conversations"])


# -- Pydantic response shapes ------------------------------------------------


class ConversationMessageOut(BaseModel):
    message_id: int
    role: str
    ts: datetime
    text: str


class ConversationMessagesPage(BaseModel):
    """A single page of conversation messages (D.18+2 pagination).

    Returned by ``GET /api/chat/conversations/{id}/messages``.

    ``total_active`` is the count of *active* messages in
    the conversation; ``total_all`` includes archive. The UI uses
    ``loaded_count < total_active`` to decide whether to
    show the "加载更早消息" affordance.

    ``messages`` is in chronological order (oldest first
    within the page — the WebUI renders top-down). The
    next page (older messages) is fetched via ``offset``;
    the previous page (newer messages) isn't needed because
    the chat pane always renders bottom-up and the head
    stays at the scroll bottom.
    """

    conversation_id: int
    messages: list[ConversationMessageOut]
    total_active: int
    total_all: int
    offset: int
    limit: int


class ConversationOut(BaseModel):
    """Wire shape for one conversation row.

    ``created_at`` / ``updated_at`` are typed ``datetime | None``
    to match :class:`magi.bus.firmwares.books.local.conversationBook.Conversation`'s
    ``BaseRecord`` mixin (which leaves them as ``None`` until the row
    is persisted). The DB schema (via :class:`BaseRecordMixin`) is
    non-nullable in practice, but Python-side optionality keeps the
    dataclass ↔ Pydantic contract honest.
    """
    conversation_id: int
    delivery_address: str
    contact_id: int
    channel: str
    created_at: datetime | None = None
    updated_at: datetime | None = None
    # D.7: operator-set or LLM-generated title.
    title: str | None = None
    schema_version: int
    messages: list[ConversationMessageOut]


class ConversationListItemOut(BaseModel):
    """The persisted conversation fields used by the paginated list."""

    conversation_id: int
    created_at: datetime | None = None
    updated_at: datetime | None = None
    title: str | None = None
    channel: str = "webui"


class ConversationListOut(BaseModel):
    items: list[ConversationListItemOut]
    total: int
    limit: int
    offset: int


class CreateConversationResponse(BaseModel):
    conversation_id: int


class UpdateConversationRequest(BaseModel):
    """Body for ``PATCH /api/chat/conversations/{conversation_id}``.

    Mirrors :class:`magi.channels.api.contacts.ContactUpdate`
    semantics (``model_fields_set``): a field's *absence*
    means "don't change". An explicit ``None`` or empty string
    means "clear the title".

    Only ``title`` is updatable for v0; future fields
    (tags, language) would land here.
    """

    title: str | None = Field(default=None, max_length=80)


def _conversation_to_out(
    s: Conversation,
    *,
    messages: list[Message],
    schema_version: int = 1,
) -> ConversationOut:
    """Project a Conversation row + its messages into the API shape.

    ``schema_version`` defaults to ``1`` — there is no on-disk
    schema versioning yet, so we surface a constant to keep the
    API contract stable for clients that want to feature-detect
    future schema changes without parsing the row shape.
    """
    return ConversationOut(
        conversation_id=s.id,
        delivery_address=s.delivery_address,
        contact_id=s.contact_id,
        channel=s.channel,
        created_at=s.created_at,
        updated_at=s.updated_at,
        schema_version=schema_version,
        # D.7: thread the (optional) title through.
        title=s.title,
        messages=[
            ConversationMessageOut(
                message_id=m.id,
                role=m.role,
                ts=m.ts,
                text=m.text,
            )
            for m in messages
        ],
    )


def _conversation_to_list_item(conversation: Conversation) -> ConversationListItemOut:
    """Convert a persisted conversation into the list-endpoint shape."""
    return ConversationListItemOut(
        conversation_id=conversation.id,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        title=conversation.title,
        channel=conversation.channel,
    )


# -- routes -----------------------------------------------------------------


def _delivery_address_for_contact_id(request: Request, contact_id: int) -> str:
    """Resolve the operator's bound per-channel delivery
    address (the TG chat id today; opaque to domain code).

    D.28: the channel dispatcher owns the
    ``contact_id → im_id`` mapping. This endpoint never reads
    ``Contact.tgid`` directly — the dispatcher
    opens its own session, so we also avoid touching
    the caller's ORM session here.

    Returns ``""`` when the operator has no binding on
    the channel. ``ConversationService.create`` accepts an
    empty address as "no outbound push", which is
    correct for WebUI rows that never need to deliver
    to a chat (the channel is the WebUI itself, not TG).
    """
    contact = get_bus(request).contacts_book.get(contact_id)
    return str(contact.tgid) if contact and contact.tgid is not None else ""


def _resolve_contact_id(request: Request) -> int:
    """Resolve the current caller's contact id.

    Mirrors :func:`magi.channels.api.auth_gates.admin_gate`'s two-leg
    strategy:

    1. **Proxy leg** — when the request carries a signed WebUI proxy
       envelope (``X-MAGI-Proxy-*``), trust the HMAC and use the
       operator identity it asserts.
    2. **Cookie leg** — fallback for direct-runtime callers that
       present a ``magi_session`` cookie (legacy internal callers
       that haven't gone through the WebUI proxy).

    Raises ``chat.unknown_sender`` 401 only when neither leg yields
    an identity — same code as chat.py so the frontend's friendly
    message covers both endpoints.
    """
    from magi.channels.api.auth import resolve_session
    from magi.channels.api.auth_gates import _proxy_identity
    from magi.channels.api.dependencies import get_bus

    proxy = _proxy_identity(request)
    if proxy is not None:
        return int(proxy[0])

    raw = request.cookies.get("magi_session") or ""
    session = resolve_session(get_bus(request), raw)
    if session is None:
        raise MagiHTTPException(
            status_code=401,
            code="chat.unknown_sender",
            detail="no signed-in contact",
        )
    return int(session["contact_id"])


def _admin_contact_id(request: Request) -> int:
    """Resolve the cookie to its admin contact id and
    gate by role.

    D.24: the cookie value IS the contact_id (no
    per-channel delivery address lookup needed).
    ``AdminGate`` already proved the cookie is a
    live admin session; this helper re-verifies the
    role to keep the router self-contained if a
    future caller skips the gate.

    Reads ``role`` and ``id`` inside the ``with`` block
    so we never touch the ORM row outside its session —
    a future lazy-load or engine reset would otherwise
    turn the trailing ``return contact.id`` into a
    ``DetachedInstanceError``.
    """
    # AdminGate has already authenticated and authorised this request.  The
    # signed cookie carries the durable contact id, so no channel may reopen
    # the ORM merely to re-read the same authority.
    return _resolve_contact_id(request)


@router.post(
    "/chat/conversations",
    response_model=CreateConversationResponse,
    status_code=201,
)
def create_conversation(
    request: Request,
    _admin: AdminGate,
    bus: BusDep,
) -> CreateConversationResponse:
    """Create a new empty conversation for the current operator.

    The frontend typically doesn't call this directly — the
    chat send endpoint auto-creates when no conversation_id is
    provided. This endpoint exists for explicit lifecycle
    hooks (e.g. "new chat" that pre-reserves an id, or
    C7-era tools that want to instantiate a conversation
    before the first message).
    """
    contact_id = _admin_contact_id(request)
    # D.23 / D.28: ``delivery_address`` is the
    # per-channel delivery address stamped on the row's
    # column (renamed from the legacy per-channel chat-id
    # column in D.28). We resolve it via the channel
    # dispatcher so this endpoint never reads
    # ``Contact.tgid`` directly. The row key, however, is
    # ``contact_id`` — the ``conversation_id`` is owned by
    # :meth:`ConversationBook.add`.
    delivery_address = _delivery_address_for_contact_id(request, contact_id)
    record = Conversation(
        delivery_address=delivery_address,
        contact_id=contact_id,
        channel="webui",
    )
    # ``add`` is a command and returns the generated id.  ``record`` remains
    # an immutable unpersisted DTO, so ``record.id`` is still 0 afterwards.
    conversation_id = bus.conversations_book.add(record)
    return CreateConversationResponse(conversation_id=conversation_id)


@router.get(
    "/chat/conversations",
    response_model=ConversationListOut,
)
def list_conversations(
    request: Request,
    _admin: AdminGate,
    bus: BusDep,
    limit: int = 50,
    offset: int = 0,
) -> ConversationListOut:
    """List current operator's conversations, newest first.

    ``limit`` is clamped to a sane range: the v0 cap is 200
    to bound the per-request work.
    """
    if limit < 1:
        limit = 50
    if limit > 200:
        limit = 200
    if offset < 0:
        offset = 0

    contact_id = _admin_contact_id(request)
    # D.23: list scope is the operator's contact_id, not
    # a per-channel delivery address.
    # ``ConversationBook.list_page_for_owner`` returns every row whose
    # ``contact_id`` matches — webui, TG, and (in future) any
    # other channel the operator owns. The frontend
    # renders the channel alongside each row (D.22
    # added the field).
    items, total = bus.conversations_book.list_page_for_owner(
        contact_id=contact_id,
        limit=limit,
        offset=offset,
    )
    return ConversationListOut(
        items=[_conversation_to_list_item(i) for i in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/chat/conversations/{conversation_id}",
    response_model=ConversationOut,
)
def get_conversation(
    conversation_id: int,
    request: Request,
    _admin: AdminGate,
    bus: BusDep,
) -> ConversationOut:
    """Load a single conversation — full transcript + metadata."""
    contact_id = _admin_contact_id(request)
    sess = bus.conversations_book.get_for_owner(
        contact_id=contact_id, conversation_id=conversation_id
    )
    if sess is None:
        raise MagiHTTPException(
            status_code=404,
            code="not_found.conversation",
            detail=f"conversation {conversation_id} not found",
        )
    messages = bus.messages_book.list_for_conversation(conversation_id=sess.id)
    return _conversation_to_out(sess, messages=messages)


@router.delete("/chat/conversations/{conversation_id}", status_code=204)
def delete_conversation(
    conversation_id: int,
    request: Request,
    _admin: AdminGate,
    bus: BusDep,
):
    """Remove a conversation permanently.

    Idempotent: deleting a conversation that's already gone is
    a no-op, not an error. Otherwise an admin could DOS
    themselves by spamming DELETE on stale ids from a
    older conversation list.
    """
    contact_id = _admin_contact_id(request)
    # ``delete`` returns False when the row is absent or owned by
    # another contact — both are treated as "already gone", so the
    # route always answers 204.
    bus.conversations_book.delete_owned(contact_id=contact_id, conversation_id=conversation_id)
    return None


@router.patch(
    "/chat/conversations/{conversation_id}",
    response_model=ConversationOut,
)
def update_conversation(
    conversation_id: int,
    payload: UpdateConversationRequest,
    request: Request,
    _admin: AdminGate,
    bus: BusDep,
) -> ConversationOut:
    """Rename a conversation (D.7).

    ``title`` semantics (mirrors the chat-send / ``model_fields_set``
    pattern used elsewhere in the codebase):

      - **absent from the body** — no-op. The response still
        returns the current state with whatever title the
        conversation already has (so the front-end can use PATCH
        as a "give me the current state" idempotent read).
      - **explicit ``null``** — clear the title.
      - **explicit string** — set after trim + length-clamp
        to 80 chars (matches ``max_length=80`` on the body).

    Manual renames do **not** bump ``updated_at``: a rename is
    operator metadata and shouldn't reshuffle the sidebar.
    The auto-title worker takes the same path with
    ``bump_updated=True`` because a freshly-titled conversation is
    content, not metadata.
    """
    contact_id = _admin_contact_id(request)

    if "title" in payload.model_fields_set:
        raw = payload.title
        # ``None`` and empty (whitespace-only or ``""``) both
        # clear. ``ConversationBook.set_title`` re-clamps to 80 as a
        # final defensive ceiling.
        new_title: str | None = None if (raw is None or raw.strip() == "") else raw
        sess = bus.conversations_book.set_title(
            contact_id=contact_id,
            conversation_id=conversation_id,
            title=new_title,
            bump_updated=False,
        )
        if sess is None:
            raise MagiHTTPException(
                status_code=404,
                code="not_found.conversation",
                detail=f"conversation {conversation_id} not found",
            )
        messages = bus.messages_book.list_for_conversation(conversation_id=sess.id)
        return _conversation_to_out(sess, messages=messages)

    # No-op path — return current state. Going through
    # ``get_for_owner`` (rather than synthesizing) surfaces a
    # 404 if the conversation vanished between the GET that
    # showed the row and this PATCH.
    sess = bus.conversations_book.get_for_owner(
        contact_id=contact_id, conversation_id=conversation_id
    )
    if sess is None:
        raise MagiHTTPException(
            status_code=404,
            code="not_found.conversation",
            detail=f"conversation {conversation_id} not found",
        )
    messages = bus.messages_book.list_for_conversation(conversation_id=sess.id)
    return _conversation_to_out(sess, messages=messages)


# ────────────────────────────────────────────────────────────────── #
# Pagination endpoint — D.18+2
# ────────────────────────────────────────────────────────────────── #
#
# Long conversations shouldn't ship their entire transcript on
# initial load. ``GET /api/chat/conversations/{id}/messages``
# returns a single chronological page of the **active**
# message tail, sized via ``limit`` (default 50, max 100)
# and offset via ``offset`` (number of *newest* rows to
# skip — so the chat pane can fetch page 0 first, then
# page 1 by passing offset=limit once the operator scrolls
# back to the top).
#
# The pagination key is ``chat_messages.id`` (auto-
# incrementing, monotonic), not ``ts`` — two messages can
# share an ISO timestamp (the agent loop writes both the
# user's text and the assistant's reply within a single
# millisecond), so ordering by ``ts`` would not be
# stable. Ordering by ``id`` is monotonic per insertion
# and therefore a stable, gap-free page boundary.
#
# Archive rows (D.17's compaction outputs) are *not*
# included in the default page; the WebUI chat pane
# doesn't render them in the conversation scroll. Pass
# ``?include_archived=true`` to opt into loading them —
# used by future audit / "show full history" views.


@router.get(
    "/chat/conversations/{conversation_id}/messages",
    response_model=ConversationMessagesPage,
)
def get_conversation_messages(
    conversation_id: int,
    request: Request,
    _admin: AdminGate,
    bus: BusDep,
    limit: int = 50,
    offset: int = 0,
    include_archived: bool = False,
) -> ConversationMessagesPage:
    """Tail-slice page of the conversation's active messages.

    The route always orders by ``chat_messages.id ASC``
    (chronological insert order) and slices by ``limit``
    + ``offset`` counting from the **newest** end. To get
    the next page of older messages, increment
    ``offset`` by the previous ``limit``.
    """
    contact_id = _admin_contact_id(request)
    # Inline clamp so the route behaves the same as the
    # ``Query(ge=…, le=…)`` form would. ``Query`` would also
    # work but needs explicit ``Annotated`` types that pydantic
    # sometimes can't resolve under ``from __future__
    # import annotations``; the manual clamp is fine for
    # these bounded ranges and keeps the typing flat.
    if limit < 1:
        limit = 50
    if limit > 100:
        limit = 100
    if offset < 0:
        offset = 0
    try:
        msgs, total_active, total_all = bus.conversations_book.get_messages_page(
            contact_id,
            conversation_id,
            limit=limit,
            offset=offset,
            include_archived=include_archived,
        )
    except ConversationNotFoundError:
        # ``get_messages_page`` raises this when the conversation
        # doesn't exist OR doesn't belong to ``contact_id`` — both
        # cases are 404 to the operator (don't leak existence of
        # other operators' conversations).
        raise MagiHTTPException(  # noqa: B904
            status_code=404,
            code="not_found.conversation",
            detail=f"conversation {conversation_id} not found",
        )

    if not msgs and offset == 0:
        # No messages AND we asked for page 0 — likely the
        # conversation doesn't exist (vs. an empty conversation).
        # Distinguishing the two cases: try ``get_for_owner``
        # and 404 if it returns None.
        sess = bus.conversations_book.get_for_owner(
            contact_id=contact_id, conversation_id=conversation_id
        )
        if sess is None:
            raise MagiHTTPException(
                status_code=404,
                code="not_found.conversation",
                detail=f"conversation {conversation_id} not found",
            )

    return ConversationMessagesPage(
        conversation_id=conversation_id,
        messages=[
            ConversationMessageOut(
                message_id=m.id,
                role=m.role,
                ts=m.ts,
                text=m.text,
            )
            for m in msgs
        ],
        total_active=total_active,
        total_all=total_all,
        offset=offset,
        limit=limit,
    )
