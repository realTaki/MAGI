"""Adam's chat endpoint — the WebUI channel's "send a
message to the LLM" route.

v0: synchronous request / response. The frontend POSTs a
text, we call :func:`magi.runtime.agent.handle_message` and
return the reply string. C7 replaces this with a streaming
endpoint (SSE or WebSocket) so the user sees tokens as they
arrive; v0 just blocks until the full reply is ready.

Per-employee LLM credentials
============================

The endpoint reads the session cookie and looks up the
Employee row whose ``telegram_id`` matches. If that row has
``provider`` + ``api_key`` configured, those are forwarded
to the agent — so an admin who set their own Minimax key
uses that key instead of the system default. Two failure
modes are treated differently on purpose:

  - **Operator has no per-employee credentials configured**
    → return ``403 chat.llm_credentials_required``. The
    frontend uses this to surface a "set your LLM provider
    first" prompt. We do NOT silently fall back to the
    system default because the operator's intent ("chat as
    *me*, not as the house bot") is the whole point of the
    per-employee credentials feature.

  - **ORM read fails (DB not initialised, etc.)**
    → return ``500 chat.lookup_failed``. The chat endpoint
    can't fulfill its job without the row, and pretending
    it can ("silently fall back") would mean the operator
    can't tell the difference between a healthy chat and
    one that can't find who they are.

The cookie / chat_id / row-exists checks are NOT done here
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

from magi.channels.webui.api.departments import AdminGate
from magi.channels.webui.api.errors import MagiHTTPException
from magi.runtime.agent import handle_message
from magi.runtime.state.orm import Employee, open_session

logger = logging.getLogger("magi.api.chat")

router = APIRouter(tags=["chat"])


# Tuned for the common case (a chat turn reply is well under
# 4K chars). If the model genuinely needs more for some
# specific task, raise this — the audit row already records
# the truncation so the operator can see it happened.
_MAX_INPUT_CHARS = 8000
_MAX_OUTPUT_CHARS = 4000


def _state_dir() -> str:
    return os.environ.get("MAGI_STATE_DIR", "/workspace/memories")


def _resolve_caller_credentials(
    state_dir: str, chat_id: str
) -> tuple[int, str, str]:
    """Look up the operator's employee row by their
    ``telegram_id`` and return ``(employee_id, provider,
    api_key)``.

    Raises ``MagiHTTPException`` rather than returning a
    sentinel:

      - ``401 chat.unknown_sender`` if the cookie's chat_id
        doesn't resolve to a row. The auth gate should have
        caught this first, but we re-check defensively so
        a future code path that skips the gate still
        fails closed.
      - ``403 chat.llm_credentials_required`` if the row
        exists but ``provider`` or ``api_key`` is unset.
        The frontend uses this code to render a "please
        configure your LLM provider first" prompt instead
        of silently using someone else's credentials.

    ORM read failures propagate as ``500 chat.lookup_failed``
    rather than being swallowed — the operator needs to
    know the chat path is broken, not silently get a
    different LLM's reply.
    """
    try:
        cid_int = int(chat_id)
    except (TypeError, ValueError):
        raise MagiHTTPException(
            status_code=401,
            code="chat.unknown_sender",
            detail="no employee row bound to this chat_id",
        )

    try:
        with open_session() as session:
            emp = session.scalar(
                select(Employee).where(Employee.telegram_id == cid_int)
            )
    except Exception:
        logger.exception(
            "chat: ORM lookup failed for chat_id %s", chat_id,
        )
        raise MagiHTTPException(
            status_code=500,
            code="chat.lookup_failed",
            detail="could not load operator's employee record",
        )

    if emp is None:
        raise MagiHTTPException(
            status_code=401,
            code="chat.unknown_sender",
            detail="no employee row bound to this chat_id",
        )
    if not emp.provider or not emp.api_key:
        logger.info(
            "chat: operator %s has no per-employee LLM credentials; "
            "asking them to configure first", emp.id,
        )
        raise MagiHTTPException(
            status_code=403,
            code="chat.llm_credentials_required",
            detail=(
                "set your LLM provider and API key in your employee "
                "profile before chatting"
            ),
        )
    return emp.id, emp.provider, emp.api_key


class ChatSendRequest(BaseModel):
    """Body for ``POST /api/chat/send``.

    Only ``text`` for v0. Future checkpoints may add
    ``conversation_id`` (to thread multi-turn chats) and
    ``model`` (to override the per-employee / system
    default) — neither is needed yet.
    """

    text: str = Field(min_length=1, max_length=_MAX_INPUT_CHARS)


class ChatSendResponse(BaseModel):
    reply: str


@router.post("/chat/send", response_model=ChatSendResponse)
async def send_chat(
    payload: ChatSendRequest,
    request: Request,
    _admin: AdminGate,
) -> ChatSendResponse:
    """Send ``text`` to the LLM and return the reply.

    The LLM is selected from the operator's Employee row
    (``provider`` + ``api_key`` set during onboarding or
    later via the employee detail panel). If those fields
    are empty the request is rejected with
    ``403 chat.llm_credentials_required`` — no silent
    fall-back to the system default. The audit row records
    the operator's ``employee_id`` regardless.
    """
    text = payload.text.strip()
    if not text:
        raise MagiHTTPException(
            status_code=400,
            code="validation.text_required",
            detail="text must not be empty",
        )

    chat_id = request.cookies.get("magi_session", "")
    # The auth gate already proved this cookie is for an
    # admin Employee row; ``_resolve_caller_credentials``
    # now strictly returns the per-emp credentials or raises
    # (no silent fall-back). The operator is told to set
    # their LLM credentials if they haven't, rather than
    # getting a reply that "isn't really theirs".
    employee_id, employee_provider, employee_api_key = (
        _resolve_caller_credentials(_state_dir(), chat_id)
    )

    reply = await handle_message(
        _state_dir(),
        text=text,
        channel="webui",
        employee_id=employee_id,
        employee_provider=employee_provider,
        employee_api_key=employee_api_key,
    )

    # Defensive truncation — the agent loop should already
    # cap via the LLM's max_tokens, but a misbehaving model
    # could still send a multi-megabyte response. We trim
    # here so the WebUI doesn't choke rendering a 5MB
    # string.
    if len(reply) > _MAX_OUTPUT_CHARS:
        reply = reply[: _MAX_OUTPUT_CHARS - 20] + "\n\n…(回复过长，已截断)"

    return ChatSendResponse(reply=reply)
