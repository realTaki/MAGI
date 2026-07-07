"""Soul editor — the WebUI surface for editing ``SOUL.md``.

The persona lives at ``<workspace_root>/SOUL.md`` and is
read on every chat turn by
:meth:`magi.agent.agent._read_soul`. There is one
``SOUL.md`` per **MAGI node** (Adam container, EVE container) —
not one per employee. Per-employee personas are C4+ and out
of scope here.

Who can edit it:

  - ``role == 'admin'`` — full access (current admin
    console users).
  - ``role == 'assigned'`` — the "served employee" of this
    MAGI node. They're the person whose chat this node
    actually drives; letting them tweak their own persona
    is the whole point of having one.
  - ``role in {'employee', 'guest'}`` — denied with 403.
    These are reserved for multi-MAGI / public-visitor
    roles (C6+) and have no business editing this node's
    persona.

Why a dedicated API surface (vs. reusing ``prompts/``):

- The *bundled* ``prompts/soul.md`` is the immutable template
  we ship with the wheel. The *workspace* ``SOUL.md`` is the
  deployer-edited copy that ``agent.py`` actually reads. They
  are physically different files in different locations.
- The Settings UI needs to know whether it's editing the
  bundled default (still "fresh") or an already-customised
  copy, so the API exposes both ``content`` and a
  ``is_bundled_fallback`` flag (true means the workspace file
  is missing and the agent is reading the generic fallback).

Atomic write: the file is rewritten via ``tempfile.mkstemp``
in the same directory + ``os.fsync`` + ``os.replace``, mirroring
:mod:`magi.agent.sessions` so a crash mid-write can never
leave a half-edited persona on disk (which the agent would
then read on the next chat turn).

The atomic write is the only durability mechanism here — v0
doesn't carry an audit log, so a successful ``update_soul``
/ ``reset_soul`` returns as soon as the file is replaced. The
file's mtime (returned as ``modified_at``) is the only
"when did the operator last change the persona" signal.
"""

from __future__ import annotations

import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Body, Request
from pydantic import BaseModel, Field

from magi.channels.webui.api.departments import AdminOrAssignedGate
from magi.agent.workspace import workspace_root

logger = logging.getLogger("magi.api.soul")

router = APIRouter(tags=["soul"])

# Upper bound on the persona text. Generous — the bundled
# default is ~300 chars and most deployer customisations land
# around 1-2 KB. 8 KB matches the chat-input cap so an operator
# who accidentally pastes a 50 KB doc into the persona editor
# gets a 422, not a 400-error chat downstream when the LLM
# provider refuses to ingest it as a system prompt.
_MAX_SOUL_CHARS = 8000

_SOUL_FILENAME = "SOUL.md"


def _state_dir() -> str:
    return os.environ.get("MAGI_STATE_DIR", "/workspace/memories")


def _soul_path() -> Path:
    return workspace_root(_state_dir()) / _SOUL_FILENAME


class SoulReadResponse(BaseModel):
    """The current persona the agent is reading.

    ``is_bundled_fallback`` is true when the workspace file is
    missing — the agent is then reading
    ``prompts/fallback_persona.md``. The Settings UI uses the
    flag to surface a "using the generic fallback; save to
    customise" warning so the operator knows the persona they
    type is going somewhere real, not overwriting a bundled
    template they're about to lose.
    """

    content: str
    # ``modified_at`` is the file's mtime, not a stored
    # timestamp field. We don't carry a separate metadata file
    # for v0; if the operator hand-edits the file outside the
    # UI the mtime will reflect that and the UI's "last edited"
    # line will still be accurate.
    modified_at: str | None
    is_bundled_fallback: bool


class SoulUpdateRequest(BaseModel):
    content: str = Field(min_length=1, max_length=_MAX_SOUL_CHARS)


class SoulUpdateResponse(BaseModel):
    modified_at: str


def _write_atomic(path: Path, content: str) -> str:
    """Atomic write to ``path``; returns ISO UTC mtime after.

    Mirrors :mod:`magi.agent.sessions` so the two file
    surfaces (sessions, soul) follow the same crash-safety
    pattern. Caller is responsible for the audit row.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent),
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    except Exception:
        # Clean up the tempfile on any failure so the dir
        # doesn't accumulate ``.SOUL.md.XXXX.tmp`` debris.
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise
    mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    return mtime.isoformat().replace("+00:00", "Z")


@router.get("/soul", response_model=SoulReadResponse)
def read_soul(_admin: AdminOrAssignedGate) -> SoulReadResponse:
    """Return the current persona text the agent reads.

    When the workspace ``SOUL.md`` is missing the agent falls
    back to ``prompts/fallback_persona.md`` — we mirror that
    behaviour here so the UI shows *what the agent is actually
    using*, not a phantom "the file is empty" state.
    """
    path = _soul_path()
    try:
        content = path.read_text(encoding="utf-8").strip()
        mtime = (
            datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            .isoformat()
            .replace("+00:00", "Z")
        )
        return SoulReadResponse(
            content=content,
            modified_at=mtime,
            is_bundled_fallback=False,
        )
    except FileNotFoundError:
        from magi.agent.prompts import load_fallback_persona
        return SoulReadResponse(
            content=load_fallback_persona(),
            modified_at=None,
            is_bundled_fallback=True,
        )


@router.put("/soul", response_model=SoulUpdateResponse)
def update_soul(
    payload: SoulUpdateRequest,
    request: Request,
    _admin: AdminOrAssignedGate,
) -> SoulUpdateResponse:
    """Persist the new persona text to ``SOUL.md``.

    The file is rewritten atomically; the agent picks up the
    new content on the next chat turn (``_read_soul`` is called
    per turn, no cache). Audit row records the SHA-256 of the
    new content so the audit trail reflects *what* changed
    without storing the whole persona twice.
    """
    content = payload.content.strip()
    if not content:
        # Pydantic's ``min_length=1`` catches the raw body,
        # but the trim happens here — refuse the post-trim
        # whitespace-only case too.
        from magi.channels.webui.api.errors import MagiHTTPException
        raise MagiHTTPException(
            status_code=400,
            code="validation.soul_empty",
            detail="persona text must contain at least one non-whitespace character",
        )

    path = _soul_path()
    modified_at = _write_atomic(path, content)

    # persona write succeeded; no audit row to maintain.
    return SoulUpdateResponse(modified_at=modified_at)


@router.post("/soul/reset", response_model=SoulUpdateResponse)
def reset_soul(
    request: Request,
    _admin: AdminOrAssignedGate,
) -> SoulUpdateResponse:
    """Reset the workspace ``SOUL.md`` to the bundled default.

    Reads the immutable ``prompts/soul.md`` and writes it to
    the workspace path. Same atomic-write as
    :func:`update_soul`.
    """
    from magi.agent.prompts import load_soul
    default = load_soul()
    path = _soul_path()
    modified_at = _write_atomic(path, default)

    return SoulUpdateResponse(modified_at=modified_at)