"""Soul editor — the WebUI surface for the managed Agent persona.

The persona lives at ``<workspace_root>/prompts/agent/soul.md`` and is
read on every chat turn by
:meth:`agent.system_prompt.read_soul`. There is one
one managed persona per **MAGI node** (ADAM container, EVA container) —
not one per contact. Per-contact personas are C4+ and out
of scope here.

Who can edit it:

  - ``role == 'admin'`` — full access (current admin
    console users).
  - ``role == 'assigned'`` — the "served contact" of this
    MAGI node. They're the person whose chat this node
    actually drives; letting them tweak their own persona
    is the whole point of having one.
  - ``role in {'contact', 'guest'}`` — denied with 403.
    These are reserved for multi-MAGI / public-visitor
    roles (C6+) and have no business editing this node's
    persona.

Reads and writes go through :class:`PromptBook` — no direct filesystem I/O
in the API layer.
"""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel, Field

from channels.api.auth_gates import AdminOrAssignedGate
from channels.api.dependencies import BusDep

logger = logging.getLogger("magi.api.soul")

router = APIRouter(tags=["soul"])

# Upper bound on the persona text. Generous — the bundled
# default is ~300 chars and most deployer customisations land
# around 1-2 KB. 8 KB matches the chat-input cap so an operator
# who accidentally pastes a 50 KB doc into the persona editor
# gets a 422, not a 400-error chat downstream when the LLM
# provider refuses to ingest it as a system prompt.
_MAX_SOUL_CHARS = 8000


class SoulReadResponse(BaseModel):
    """The current persona the agent is reading.

    ``is_bundled_fallback`` is true when the workspace file is
    missing — the agent is then reading
    generic managed fallback. The Settings UI uses the
    flag to surface a "using the generic fallback; save to
    customise" warning so the operator knows the persona they
    type is going somewhere real, not overwriting a bundled
    template they're about to lose.
    """

    content: str
    modified_at: datetime | None
    is_bundled_fallback: bool


class SoulUpdateRequest(BaseModel):
    content: str = Field(min_length=1, max_length=_MAX_SOUL_CHARS)


class SoulUpdateResponse(BaseModel):
    modified_at: datetime


# -- endpoints ---------------------------------------------------------


@router.get("/soul", response_model=SoulReadResponse)
def read_soul(_admin: AdminOrAssignedGate, bus: BusDep) -> SoulReadResponse:
    """Return the current persona text the agent reads.

    When the managed persona is missing the agent falls back to the Agent
    default — we mirror that
    behaviour here so the UI shows *what the agent is actually
    using*, not a phantom "the file is empty" state.
    """
    content = bus.prompt_book.get(key="agent/soul") or ""
    is_bundled_fallback = "agent/soul" not in bus.prompt_book.list()
    return SoulReadResponse(
        content=content,
        modified_at=None,
        is_bundled_fallback=is_bundled_fallback,
    )


@router.put("/soul", response_model=SoulUpdateResponse)
def update_soul(
    payload: SoulUpdateRequest,
    _admin: AdminOrAssignedGate,
    bus: BusDep,
) -> SoulUpdateResponse:
    """Persist the new persona through ``PromptBook``.

    The file is rewritten atomically (via
    :meth:`PromptBook.set`); the agent picks up
    the new content on the next chat turn (``read_soul`` is called
    per turn, no cache).
    """
    content = payload.content.strip()
    if not content:
        # Pydantic's ``min_length=1`` catches the raw body,
        # but the trim happens here — refuse the post-trim
        # whitespace-only case too.
        from channels.api.errors import MagiHTTPException

        raise MagiHTTPException(
            status_code=400,
            code="validation.soul_empty",
            detail="persona text must contain at least one non-whitespace character",
        )

    assert bus.prompt_book is not None
    modified_at = bus.prompt_book.set(key="agent/soul", value=content)
    return SoulUpdateResponse(modified_at=modified_at)


@router.post("/soul/reset", response_model=SoulUpdateResponse)
def reset_soul(
    _admin: AdminOrAssignedGate,
    bus: BusDep,
) -> SoulUpdateResponse:
    """Reset the managed persona to the AgentWorker default."""
    assert bus.prompt_book is not None
    try:
        modified_at = bus.prompt_book.reset(key="agent/soul")
    except KeyError:
        from channels.api.errors import MagiHTTPException

        raise MagiHTTPException(
            status_code=503,
            code="prompt.default_missing",
            detail="AgentWorker default soul is not registered",
        ) from None
    return SoulUpdateResponse(modified_at=modified_at)
