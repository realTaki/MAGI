"""``/api/skills`` — skill registry with enable/disable.

The actual machine-readable catalog lives in
::mod:`magi.agent.skills.loader` and is a module
singleton. This router wraps it for the WebUI / admin
consoles. Disabled skills are persisted in the
``settings`` table under ``skills.disabled`` as a
JSON array of skill names.

Endpoints
---------

- ``GET /api/skills``                       → list of skill metadata
- ``PATCH /api/skills/{name}``             → toggle enabled
- ``GET /api/skills/{name}/raw``           → markdown body

Auth: admin-gated like every other Adam endpoint.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel

from magi.bus import bootstrap
from magi.channels.webui.api.auth_gates import AdminGate
from magi.channels.webui.api.errors import MagiHTTPException
from magi.skills import get_skill_metas

logger = logging.getLogger("magi.channels.webui.api.skills")

router = APIRouter(tags=["skills"])

_NAME_RE = re.compile(r"^[a-zA-Z0-9_.\-]{1,64}$")
_DISABLED_KEY = "skills.disabled"


def _bus():
    return bootstrap(os.environ.get("MAGI_STATE_DIR", ""))


def _load_disabled() -> set[str]:
    raw = _bus().settings.get(_DISABLED_KEY)
    if not raw:
        return set()
    try:
        return set(json.loads(raw))
    except (json.JSONDecodeError, TypeError):
        return set()


def _save_disabled(disabled: set[str]) -> None:
    _bus().settings.set(_DISABLED_KEY, json.dumps(sorted(disabled)))


_MAX_BODY_BYTES = 32 * 1024


class SkillOut(BaseModel):
    name: str
    description: str
    path: str
    version: Optional[str] = None
    enabled: bool = True


class SkillBodyOut(BaseModel):
    name: str
    content: str
    modified_at: str
    truncated: bool


class SkillToggleIn(BaseModel):
    enabled: bool


@router.get("/skills", response_model=list[SkillOut])
def list_skills(
    request: Request,
    _admin: AdminGate,
) -> list[SkillOut]:
    """Enumerate every registered skill."""
    loader = get_skill_metas()
    disabled = _load_disabled()
    return [
        SkillOut(
            name=s.name,
            description=s.description,
            path=str(s.path),
            version=s.version,
            enabled=s.name not in disabled,
        )
        for s in loader.list()
    ]


@router.patch("/skills/{name}", response_model=SkillOut)
def toggle_skill(
    request: Request,
    name: str,
    body: SkillToggleIn,
    _admin: AdminGate,
) -> SkillOut:
    """Enable or disable a skill."""
    if not _NAME_RE.match(name):
        raise MagiHTTPException(status_code=400, code="validation.skill_name", detail="invalid skill name")
    loader = get_skill_metas()
    meta = loader.get(name)
    if meta is None:
        raise MagiHTTPException(status_code=404, code="not_found.skill", detail=f"skill {name!r} not registered")
    disabled = _load_disabled()
    if body.enabled:
        disabled.discard(name)
    else:
        disabled.add(name)
    _save_disabled(disabled)
    return SkillOut(
        name=meta.name,
        description=meta.description,
        path=str(meta.path),
        version=meta.version,
        enabled=body.enabled,
    )


@router.get("/skills/{name}/raw", response_model=SkillBodyOut)
def get_skill_body(
    request: Request,
    name: str,
    _admin: AdminGate,
) -> SkillBodyOut:
    """Return the SKILL.md markdown body for ``name``."""
    if not _NAME_RE.match(name):
        raise MagiHTTPException(status_code=400, code="validation.skill_name", detail="invalid skill name")
    loader = get_skill_metas()
    meta = loader.get(name)
    if meta is None:
        raise MagiHTTPException(status_code=404, code="not_found.skill", detail=f"skill {name!r} not registered")
    try:
        raw_bytes = meta.path.read_bytes()
    except OSError as exc:
        logger.warning("get_skill_body: read failed: %s", exc)
        raise MagiHTTPException(status_code=500, code="skill.read_failed", detail="read failed") from exc
    truncated = len(raw_bytes) > _MAX_BODY_BYTES
    if truncated:
        body_bytes = raw_bytes[:_MAX_BODY_BYTES]
        while body_bytes and (body_bytes[-1] & 0xC0) == 0x80:
            body_bytes = body_bytes[:-1]
        content = body_bytes.decode("utf-8", errors="replace") + "\n\n…[truncated]"
    else:
        content = raw_bytes.decode("utf-8", errors="replace")
    mtime = datetime.fromtimestamp(meta.path.stat().st_mtime, tz=timezone.utc).isoformat()
    return SkillBodyOut(name=name, content=content, modified_at=mtime, truncated=truncated)
