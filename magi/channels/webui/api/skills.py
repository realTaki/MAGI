"""``/api/skills`` — read-only skill registry surface.

The actual machine-readable catalog lives in
:mod:`magi.runtime.skills.loader` and is a module
singleton. This router just wraps it for the WebUI /
admin consoles — no caching, no live-rewatch; the loader
itself caches once at process start. A successful
operator of a SKILL.md restarts the node to see it.

Endpoints
---------

- ``GET /api/skills``                       → list of skill
                                                metadata rows
- ``GET /api/skills/{name}/raw``           → markdown body
                                                (audit / future
                                                editor only)

Auth: admin-gated like every other Adam endpoint.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from magi.channels.webui.api.departments import AdminGate, get_session
from magi.channels.webui.api.errors import MagiHTTPException
from magi.runtime.skills import get_skill_loader

logger = logging.getLogger("magi.channels.webui.api.skills")

router = APIRouter(tags=["skills"])

_NAME_RE = re.compile(r"^[a-zA-Z0-9_.\-]{1,64}$")


# Same lower bound the tool path enforces; ``load_skill``
# tool runs in the LLM-callable API and reuses this
# threshold. Clients of both surfaces (UI + LLM) share
# the constraint.
_MAX_BODY_BYTES = 32 * 1024


class SkillOut(BaseModel):
    name: str
    description: str
    path: str
    version: Optional[str] = None


class SkillBodyOut(BaseModel):
    name: str
    content: str
    modified_at: str
    truncated: bool


@router.get("/skills", response_model=list[SkillOut])
def list_skills(
    request: Request,
    _admin: AdminGate,
    session: Session = Depends(get_session),
) -> list[SkillOut]:
    """Enumerate every registered skill.

    Sorted by name (already done by the loader) so the
    WebUI doesn't have to. Path is serialised as a
    string — the UI uses it for the "where on disk is
    this?" tooltip, never for navigation.
    """
    loader = get_skill_loader()
    return [
        SkillOut(
            name=s.name,
            description=s.description,
            path=str(s.path),
            version=s.version,
        )
        for s in loader.list()
    ]


@router.get("/skills/{name}/raw", response_model=SkillBodyOut)
def get_skill_body(
    request: Request,
    name: str,
    _admin: AdminGate,
    session: Session = Depends(get_session),
) -> SkillBodyOut:
    """Return the SKILL.md markdown body for ``name``.

    Used by the audit console today (a future editor
    may stream-edit through ``PUT``; not in v0). Body
    is truncated at 32 KB to match the ``load_skill`` tool
    ceiling — keeps the two surfaces consistent in what's
    "the full body".
    """
    if not _NAME_RE.match(name):
        raise MagiHTTPException(
            status_code=400, code="validation.skill_name",
            detail="invalid skill name",
        )
    loader = get_skill_loader()
    meta = loader.get(name)
    if meta is None:
        raise MagiHTTPException(
            status_code=404, code="not_found.skill",
            detail=f"skill {name!r} not registered",
        )
    try:
        raw_bytes = meta.path.read_bytes()
    except OSError as exc:
        logger.warning("get_skill_body: read failed: %s", exc)
        raise MagiHTTPException(
            status_code=500, code="skill.read_failed",
            detail="read failed",
        ) from exc
    truncated = len(raw_bytes) > _MAX_BODY_BYTES
    if truncated:
        body_bytes = raw_bytes[:_MAX_BODY_BYTES]
        while body_bytes and (body_bytes[-1] & 0xC0) == 0x80:
            body_bytes = body_bytes[:-1]
        content = body_bytes.decode("utf-8", errors="replace") + "\n\n…[truncated]"
    else:
        content = raw_bytes.decode("utf-8", errors="replace")
    mtime = datetime.fromtimestamp(meta.path.stat().st_mtime, tz=timezone.utc).isoformat()
    return SkillBodyOut(
        name=name,
        content=content,
        modified_at=mtime,
        truncated=truncated,
    )
