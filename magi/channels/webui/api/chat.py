"""Adam's chat endpoint — the WebUI channel's "send a
message to the LLM" route.

v0: synchronous request / response. The frontend POSTs a
text, we call :func:`magi.runtime.agent.handle_message` and
return the reply string. C7 replaces this with a streaming
endpoint (SSE or WebSocket) so the user sees tokens as they
arrive; v0 just blocks until the full reply is ready.

The endpoint is the second consumer of ``handle_message``
(the first being the TG channel). Both call the same
function with different ``channel`` tags so the audit row
distinguishes the source. No employee binding for the
WebUI chat — the operator's outbound messages go through
the system default LLM, with no per-employee override.

Anti-abuse: the request body is bounded (max 8K text) and
the reply is bounded (max 4K text, same as TG). The LLM
has its own ``max_tokens`` cap; the 4K byte cap is a
defensive ceiling on top.
"""

from __future__ import annotations

import os

from fastapi import APIRouter
from pydantic import BaseModel, Field

from magi.channels.webui.api.errors import MagiHTTPException

from magi.channels.webui.api.departments import AdminGate
from magi.runtime.agent import handle_message

router = APIRouter(tags=["chat"])


# Tuned for the common case (a chat turn reply is well under
# 4K chars). If the model genuinely needs more for some
# specific task, raise this — the audit row already records
# the truncation so the operator can see it happened.
_MAX_INPUT_CHARS = 8000
_MAX_OUTPUT_CHARS = 4000


def _state_dir() -> str:
    return os.environ.get("MAGI_STATE_DIR", "/workspace/memories")


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
    _admin: AdminGate,
) -> ChatSendResponse:
    """Send ``text`` to the system LLM and return the reply.

    No employee binding — the WebUI chat is operator-to-
    system, not operator-to-EVE. To chat "as" a specific
    employee, the operator would use a future EVE-console
    feature (not in v0).
    """
    text = payload.text.strip()
    if not text:
        raise MagiHTTPException(
            status_code=400,
            code="validation.text_required",
            detail="text must not be empty",
        )

    reply = await handle_message(
        _state_dir(),
        text=text,
        channel="webui",
        employee_id=None,
    )

    # Defensive truncation — the agent loop should already
    # cap via the LLM's max_tokens, but a misbehaving model
    # could still send a multi-megabyte response. We trim
    # here so the WebUI doesn't choke rendering a 5MB
    # string.
    if len(reply) > _MAX_OUTPUT_CHARS:
        reply = reply[: _MAX_OUTPUT_CHARS - 20] + "\n\n…(回复过长，已截断)"

    return ChatSendResponse(reply=reply)
