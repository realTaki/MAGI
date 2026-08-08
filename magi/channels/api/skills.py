"""``/api/skills`` — skill registry with enable/disable.

The machine-readable catalog lives on the new_bus as
``bus.skills_book`` (:mod:`magi.new_bus.library.file.skillsBook`);
this router wraps it for the WebUI / admin consoles. Disabled
skills are persisted in the ``settings`` table under
``skills.disabled`` as a JSON array of skill names.

Endpoints
---------

- ``GET /api/skills``                       → list of skill metadata
- ``PATCH /api/skills/{name}``             → toggle enabled
- ``GET /api/skills/{name}/raw``           → markdown body

Auth: admin-gated like every other ADAM endpoint.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from magi.bus import get_bus
from magi.channels.api.auth_gates import AdminGate
from magi.channels.api.errors import MagiHTTPException
from magi.new_bus import get_new_bus

logger = logging.getLogger("magi.channels.api.skills")

router = APIRouter(tags=["skills"])

_NAME_RE = re.compile(r"^[a-zA-Z0-9_.\-]{1,64}$")
_DISABLED_KEY = "skills.disabled"


def _bus():
    return get_bus()


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
    _admin: AdminGate,
) -> list[SkillOut]:
    """Enumerate every registered skill."""
    book = get_new_bus().skills_book
    disabled = _load_disabled()
    return [
        SkillOut(
            name=s.name,
            description=s.description,
            path=str(s.path),
            version=s.version,
            enabled=s.name not in disabled,
        )
        for s in book.list()
    ]


@router.patch("/skills/{name}", response_model=SkillOut)
def toggle_skill(
    name: str,
    body: SkillToggleIn,
    _admin: AdminGate,
) -> SkillOut:
    """Enable or disable a skill."""
    if not _NAME_RE.match(name):
        raise MagiHTTPException(status_code=400, code="validation.skill_name", detail="invalid skill name")
    book = get_new_bus().skills_book
    meta = book.get(name)
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
    name: str,
    _admin: AdminGate,
) -> SkillBodyOut:
    """Return the SKILL.md markdown body for ``name``."""
    if not _NAME_RE.match(name):
        raise MagiHTTPException(status_code=400, code="validation.skill_name", detail="invalid skill name")
    book = get_new_bus().skills_book
    meta = book.get(name)
    if meta is None:
        raise MagiHTTPException(status_code=404, code="not_found.skill", detail=f"skill {name!r} not registered")
    try:
        body = book.read_body(name)
    except OSError as exc:
        logger.warning("get_skill_body: read failed: %s", exc)
        raise MagiHTTPException(status_code=500, code="skill.read_failed", detail="read failed") from exc
    return SkillBodyOut(
        name=name,
        content=body.content,
        modified_at=body.mtime.isoformat().replace("+00:00", "Z"),
        truncated=body.truncated,
    )