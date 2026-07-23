"""CRUD endpoints for chat sessions.

A "session" is a single thread of messages between an
operator (identified by their TG tgid / dashboard cookie)
and the system LLM. Sessions are persisted as JSON files
under the operator's workspace (see :mod:`magi.agent.memory.session`)
and are per-user — admin A's session is invisible to admin B.

Endpoints
---------

- ``POST   /chat/sessions``              create empty session, return id
- ``GET    /chat/sessions``              list current operator's sessions
- ``GET    /chat/sessions/{session_id}``  load a single session (full messages)
- ``DELETE /chat/sessions/{session_id}``  remove a session

The ``{session_id}`` route uses the URL as the only
identification: there is no separate ``tgid`` parameter,
because the cookie already pins the caller. The tgid is
derived from the cookie via :func:`_current_admin_tgid`.
"""

from __future__ import annotations

import logging
import os
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from magi.channels.webui.api.departments import AdminGate
from magi.channels.webui.api.errors import MagiHTTPException
from magi.agent.memory.session import (
    Session,
    SessionCorruptError,
    SessionError,
    SessionMessage,
    SessionNotFoundError,
    SessionPathError,
    SessionStore,
    SessionSummary,
    new_session_id,
)
from magi.agent.db import Employee, open_session, require_state_dir

logger = logging.getLogger("magi.api.chat_sessions")

router = APIRouter(tags=["chat_sessions"])


def _state_dir() -> str:
    return require_state_dir()


def get_session_store() -> SessionStore:
    """FastAPI dependency — one SessionStore per request.

    We deliberately construct it lazily (per-request) rather
    than at module import: tests that override
    ``MAGI_WORKSPACE_DIR`` need each request to see the
    current value, not the value captured at import time.
    """
    return SessionStore(_state_dir())


SessionStoreDep = Annotated[SessionStore, Depends(get_session_store)]


# -- Pydantic response shapes ------------------------------------------------


class SessionMessageOut(BaseModel):
    message_id: str
    role: str
    ts: str
    text: str


class SessionMessagesPage(BaseModel):
    """A single page of session messages (D.18+2 pagination).

    Returned by ``GET /api/chat/sessions/{id}/messages``.

    ``total_active`` is the count of *active* messages in
    the session; ``total_all`` includes archive. The UI uses
    ``loaded_count < total_active`` to decide whether to
    show the "加载更早消息" affordance.

    ``messages`` is in chronological order (oldest first
    within the page — the WebUI renders top-down). The
    next page (older messages) is fetched via ``offset``;
    the previous page (newer messages) isn't needed because
    the chat pane always renders bottom-up and the head
    stays at the scroll bottom.
    """

    session_id: str
    messages: list[SessionMessageOut]
    total_active: int
    total_all: int
    offset: int
    limit: int


class SessionOut(BaseModel):
    session_id: str
    tgid: str
    uid: int
    channel: str
    created_at: str
    updated_at: str
    # D.7: operator-set or LLM-generated title. ``None`` means
    # "no title yet" — the sidebar falls back to ``preview``.
    title: str | None = None
    schema_version: int
    messages: list[SessionMessageOut]


class SessionSummaryOut(BaseModel):
    session_id: str
    created_at: str
    created_by_uid: int
    updated_at: str
    message_count: int
    preview: str
    # D.7: same field as ``Session.title`` — list-endpoint
    # projection.
    title: str | None = None


class SessionListOut(BaseModel):
    items: list[SessionSummaryOut]
    total: int
    limit: int
    offset: int


class CreateSessionResponse(BaseModel):
    session_id: str


class UpdateSessionRequest(BaseModel):
    """Body for ``PATCH /api/chat/sessions/{session_id}``.

    Mirrors :class:`magi.channels.webui.api.departments.EmployeeUpdate`
    semantics (``model_fields_set``): a field's *absence*
    means "don't change". An explicit ``None`` or empty string
    means "clear the title".

    Only ``title`` is updatable for v0; future fields
    (tags, language) would land here.
    """

    title: str | None = Field(default=None, max_length=80)


def _session_to_out(s: Session) -> SessionOut:
    return SessionOut(
        session_id=s.session_id,
        tgid=s.tgid,
        uid=s.uid,
        channel=s.channel,
        created_at=s.created_at,
        updated_at=s.updated_at,
        schema_version=s.schema_version,
        # D.7: thread the (optional) title through.
        title=s.title,
        messages=[
            SessionMessageOut(
                message_id=m.message_id,
                role=m.role,
                ts=m.ts,
                text=m.text,
            )
            for m in s.messages
        ],
    )


def _summary_to_out(s: SessionSummary, *, uid: int) -> SessionSummaryOut:
    """Convert a SessionSummary into the list-endpoint shape.

    ``uid`` is the operator who owns this tgid
    today. We surface it explicitly so a future C7 view can
    label rows; v0 always sees the same value across rows
    for one admin.
    """
    return SessionSummaryOut(
        session_id=s.session_id,
        created_at=s.created_at,
        created_by_uid=uid,
        updated_at=s.updated_at,
        message_count=s.message_count,
        preview=s.preview,
        # D.7: surface the title alongside the preview so the
        # front-end can render ``h.title ?? h.preview``.
        title=s.title,
    )


# -- routes -----------------------------------------------------------------


def _telegram_id_str_for_uid(uid: int) -> str:
    """Look up the operator's bound TG tgid as a string.

    D.24: the cookie identity is the uid, but
    ``SessionStore.create`` still takes a ``tgid=``
    keyword that stamps the per-channel delivery address
    on the row's ``tgid`` column. WebUI rows that
    originated here get the operator's bound TG chat id
    (or ``""`` if the operator never bound one) so future
    cross-channel tooling can still address the bot.

    Cheap one-shot ORM read — the row is already in the
    session cache by the time we get here from the
    AdminGate / cookie checks above. The previous shape
    accessed ``emp.telegram_id`` outside the ``with``
    block — works today because the column is eager, but
    it's a detached-instance trap: a future change to
    lazy-load (or an ORM-engine reset between requests)
    would turn this into a ``DetachedInstanceError``.
    Reading the scalar inside the block and returning a
    plain ``str`` kills the trap at the source.
    """
    with open_session() as session:
        emp = session.get(Employee, uid)
        telegram_id = emp.telegram_id if emp is not None else None
    if telegram_id is None:
        return ""
    return str(telegram_id)


def _resolve_uid(request: Request) -> int:
    """Resolve the cookie's ``magi_session`` value to the
    current employee's id.

    D.24: the cookie carries the **uid** (stringified
    int) — not the TG tgid. This helper is the single
    place that translates "what's in the cookie" into
    "who is the caller" for the rest of the chat_sessions
    router. Raises ``chat.unknown_sender`` 401 if the
    cookie is missing or unparseable — same code as
    chat.py so the frontend's friendly message covers
    both endpoints.
    """
    raw = request.cookies.get("magi_session") or ""
    try:
        eid = int(raw)
    except (TypeError, ValueError):
        raise MagiHTTPException(
            status_code=401,
            code="chat.unknown_sender",
            detail="no signed-in employee",
        )
    return eid


def _admin_uid(request: Request, store: SessionStoreDep) -> int:
    """Resolve the cookie to its admin employee id and
    gate by role.

    D.24: the cookie value IS the uid (no
    telegram_id lookup needed). ``AdminGate`` already
    proved the cookie is a live admin session; this
    helper re-verifies the role to keep the router
    self-contained if a future caller skips the gate.

    Reads ``role`` and ``id`` inside the ``with`` block
    so we never touch the ORM row outside its session —
    a future lazy-load or engine reset would otherwise
    turn the trailing ``return emp.id`` into a
    ``DetachedInstanceError``.
    """
    uid = _resolve_uid(request)
    with open_session() as session:
        emp = session.get(Employee, uid)
        if emp is None or emp.role != "admin":
            raise MagiHTTPException(
                status_code=401,
                code="chat.unknown_sender",
                detail="no admin employee row bound to this session",
            )
        return emp.id


@router.post(
    "/chat/sessions",
    response_model=CreateSessionResponse,
    status_code=201,
)
def create_session(
    request: Request,
    _admin: AdminGate,
    store: SessionStoreDep,
) -> CreateSessionResponse:
    """Create a new empty session for the current operator.

    The frontend typically doesn't call this directly — the
    chat send endpoint auto-creates when no session_id is
    provided. This endpoint exists for explicit lifecycle
    hooks (e.g. "new chat" that pre-reserves an id, or
    C7-era tools that want to instantiate a session
    before the first message).
    """
    uid = _admin_uid(request, store)
    # D.23: ``tgid`` is the per-channel delivery
    # address stamped on the row's ``tgid`` column. We
    # still carry the cookie's telegram_id here so a
    # future cross-channel query tool can use the row's
    # tgid to address the operator's TG bot. The store
    # key, however, is ``uid`` — see
    # :meth:`SessionStore.create`.
    tgid = _telegram_id_str_for_uid(uid)
    sess = store.create(
        uid, channel="webui", tgid=tgid,
    )
    return CreateSessionResponse(session_id=sess.session_id)


@router.get(
    "/chat/sessions",
    response_model=SessionListOut,
)
def list_sessions(
    request: Request,
    _admin: AdminGate,
    store: SessionStoreDep,
    limit: int = 50,
    offset: int = 0,
) -> SessionListOut:
    """List current operator's sessions, newest first.

    ``limit`` is clamped to a sane range: the v0 cap is 200
    to bound the per-request work (the implementation
    reads every session file under the chat's directory —
    fine for a single operator's tens-to-hundreds of
    sessions, but a misbehaving client cannot push it
    further).
    """
    if limit < 1:
        limit = 50
    if limit > 200:
        limit = 200
    if offset < 0:
        offset = 0

    uid = _admin_uid(request, store)
    # D.23: list scope is the operator's uid, not
    # the cookie's tgid. ``store.list_summaries``
    # returns every row whose ``uid`` matches —
    # webui, TG, and (in future) any other channel the
    # operator owns. The frontend renders the channel
    # alongside each row (D.22 added the field).
    items, total = store.list_summaries(
        uid, limit=limit, offset=offset,
    )
    return SessionListOut(
        items=[
            _summary_to_out(i, uid=uid)
            for i in items
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/chat/sessions/{session_id}",
    response_model=SessionOut,
)
def get_session(
    session_id: str,
    request: Request,
    _admin: AdminGate,
    store: SessionStoreDep,
) -> SessionOut:
    """Load a single session — full transcript + metadata."""
    uid = _admin_uid(request, store)
    try:
        sess = store.get(uid, session_id)
    except SessionPathError as e:
        # Malformed session_id from the URL — it's a 400,
        # not a 404 (the id is invalid, not absent).
        logger.warning(
            "session get rejected (bad session_id %r from employee %s): %s",
            session_id, uid, e,
        )
        raise MagiHTTPException(
            status_code=400,
            code="validation.session_id_invalid",
            detail=str(e),
        )
    except SessionCorruptError as e:
        logger.error("session file corrupt: %s", e)
        raise MagiHTTPException(
            status_code=500,
            code="validation.session_corrupt",
            detail="session file is malformed",
        )
    if sess is None:
        raise MagiHTTPException(
            status_code=404,
            code="not_found.session",
            detail=f"session {session_id} not found",
        )
    return _session_to_out(sess)


@router.delete("/chat/sessions/{session_id}", status_code=204)
def delete_session(
    session_id: str,
    request: Request,
    _admin: AdminGate,
    store: SessionStoreDep,
):
    """Remove a session permanently.

    Idempotent: deleting a session that's already gone is
    a no-op, not an error. Otherwise an admin could DOS
    themselves by spamming DELETE on stale ids from a
    older session list.
    """
    uid = _admin_uid(request, store)
    try:
        removed = store.delete(uid, session_id)
    except SessionPathError as e:
        raise MagiHTTPException(
            status_code=400,
            code="validation.session_id_invalid",
            detail=str(e),
        )
    if not removed:
        # The 204 status is set by the response_model /
        # response_class on the route. For clarity, we
        # ALWAYS return 204 — see the comment above.
        return None
    return None


@router.patch(
    "/chat/sessions/{session_id}",
    response_model=SessionOut,
)
def update_session(
    session_id: str,
    payload: UpdateSessionRequest,
    request: Request,
    _admin: AdminGate,
    store: SessionStoreDep,
) -> SessionOut:
    """Rename a session (D.7).

    ``title`` semantics (mirrors the chat-send / ``model_fields_set``
    pattern used elsewhere in the codebase):

      - **absent from the body** — no-op. The response still
        returns the current state with whatever title the
        session already has (so the front-end can use PATCH
        as a "give me the current state" idempotent read).
      - **explicit ``null``** — clear the title.
      - **explicit string** — set after trim + length-clamp
        to 80 chars (matches ``max_length=80`` on the body).

    Manual renames do **not** bump ``updated_at``: a rename is
    operator metadata and shouldn't reshuffle the sidebar.
    The auto-title worker takes the same ``rename`` path with
    ``bump_updated=True`` because a freshly-titled session is
    content, not metadata.
    """
    uid = _admin_uid(request, store)

    if "title" in payload.model_fields_set:
        raw = payload.title
        # ``None`` and empty (whitespace-only or ``""``) both
        # clear. ``SessionStore.rename`` re-clamps to 80 as a
        # final defensive ceiling.
        if raw is None or raw.strip() == "":
            new_title: str | None = None
        else:
            new_title = raw

        try:
            sess = store.rename(
                uid, session_id, new_title, bump_updated=False
            )
        except SessionPathError as e:
            raise MagiHTTPException(
                status_code=400,
                code="validation.session_id_invalid",
                detail=str(e),
            )
        except SessionCorruptError as e:
            logger.error(
                "rename failed: session file corrupt: %s", e,
                extra={"session_id": session_id},
            )
            raise MagiHTTPException(
                status_code=500,
                code="validation.session_corrupt",
                detail="session file is malformed",
            )
        except SessionNotFoundError:
            raise MagiHTTPException(
                status_code=404,
                code="not_found.session",
                detail=f"session {session_id} not found",
            )
        return _session_to_out(sess)

    # No-op path — return current state. Going through
    # ``store.get`` (rather than synthesizing) surfaces a
    # 404 if the session vanished between the GET that
    # showed the row and this PATCH.
    try:
        sess = store.get(uid, session_id)
    except SessionPathError as e:
        raise MagiHTTPException(
            status_code=400,
            code="validation.session_id_invalid",
            detail=str(e),
        )
    except SessionCorruptError as e:
        logger.error(
            "get failed: session file corrupt: %s", e,
            extra={"session_id": session_id},
        )
        raise MagiHTTPException(
            status_code=500,
            code="validation.session_corrupt",
            detail="session file is malformed",
        )
    if sess is None:
        raise MagiHTTPException(
            status_code=404,
            code="not_found.session",
            detail=f"session {session_id} not found",
        )
    return _session_to_out(sess)


# ────────────────────────────────────────────────────────────────── #
# Pagination endpoint — D.18+2
# ────────────────────────────────────────────────────────────────── #
#
# Long sessions shouldn't ship their entire transcript on
# initial load. ``GET /api/chat/sessions/{id}/messages``
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
    "/chat/sessions/{session_id}/messages",
    response_model=SessionMessagesPage,
)
def get_session_messages(
    session_id: str,
    request: Request,
    _admin: AdminGate,
    store: SessionStoreDep,
    limit: int = 50,
    offset: int = 0,
    include_archived: bool = False,
) -> SessionMessagesPage:
    """Tail-slice page of the session's active messages.

    The route always orders by ``chat_messages.id ASC``
    (chronological insert order) and slices by ``limit``
    + ``offset`` counting from the **newest** end. To get
    the next page of older messages, increment
    ``offset`` by the previous ``limit``.
    """
    uid = _admin_uid(request, store)
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
        msgs, total_active, total_all = store.get_messages_page(
            uid, session_id,
            limit=limit, offset=offset,
            include_archived=include_archived,
        )
    except SessionPathError as e:
        raise MagiHTTPException(
            status_code=400,
            code="validation.session_id_invalid",
            detail=str(e),
        )

    if not msgs and offset == 0:
        # No messages AND we asked for page 0 — likely the
        # session doesn't exist (vs. an empty session).
        # Distinguishing the two cases: try ``store.get``
        # and 404 if it returns None.
        sess = store.get(uid, session_id)
        if sess is None:
            raise MagiHTTPException(
                status_code=404,
                code="not_found.session",
                detail=f"session {session_id} not found",
            )

    return SessionMessagesPage(
        session_id=session_id,
        messages=[
            SessionMessageOut(
                message_id=m.message_id,
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
