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

Auth: admin-gated like every other ADAM endpoint.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel

from magi.old_bus import Bus
from magi.old_bus.firmwares.books.file.skillsBook import SkillBookError, SkillNotFound, SkillsBook
from magi.channels.api.auth_gates import AdminGate
from magi.channels.api.dependencies import BusDep
from magi.channels.api.errors import MagiHTTPException

logger = logging.getLogger("magi.channels.api.skills")

router = APIRouter(tags=["skills"])

_NAME_RE = re.compile(r"^[a-zA-Z0-9_.\-]{1,64}$")
_DISABLED_KEY = "skills.disabled"


def _skills_book(bus: Bus) -> SkillsBook:
    """Return the skills Book, or 503 when no skills root is configured.

    ``Bus.skills_book`` is ``SkillsBook | None`` — it is only populated
    when a skills directory was resolved at bootstrap. Every route here
    is meaningless without it, so a missing Book is a deployment-state
    error rather than an ``AttributeError`` on ``None``.
    """
    if bus.skills_book is None:
        raise MagiHTTPException(
            status_code=503,
            code="unavailable.skills_store",
            detail="Skills are not available on this node",
        )
    return bus.skills_book


def _load_disabled(bus: Bus) -> set[str]:
    raw = bus.settings_book.get_value(key=_DISABLED_KEY)
    if not raw:
        return set()
    try:
        return set(json.loads(raw))
    except (json.JSONDecodeError, TypeError):
        return set()


def _save_disabled(bus: Bus, disabled: set[str]) -> None:
    bus.settings_book.set(key=_DISABLED_KEY, value=json.dumps(sorted(disabled)))


class SkillOut(BaseModel):
    name: str
    description: str
    path: str
    version: str | None = None
    enabled: bool = True


class SkillBodyOut(BaseModel):
    name: str
    content: str
    modified_at: datetime
    truncated: bool


class SkillToggleIn(BaseModel):
    enabled: bool


@router.get("/skills", response_model=list[SkillOut])
def list_skills(
    _admin: AdminGate,
    bus: BusDep,
) -> list[SkillOut]:
    """Enumerate every registered skill."""
    disabled = _load_disabled(bus)
    return [
        SkillOut(
            name=s.name,
            description=s.description,
            path=str(s.path),
            version=s.version,
            enabled=s.name not in disabled,
        )
        for s in _skills_book(bus).list()
    ]


@router.patch("/skills/{name}", response_model=SkillOut)
def toggle_skill(
    name: str,
    body: SkillToggleIn,
    _admin: AdminGate,
    bus: BusDep,
) -> SkillOut:
    """Enable or disable a skill."""
    if not _NAME_RE.match(name):
        raise MagiHTTPException(
            status_code=400, code="validation.skill_name", detail="invalid skill name"
        )
    meta = _skills_book(bus).get(name)
    if meta is None:
        raise MagiHTTPException(
            status_code=404, code="not_found.skill", detail=f"skill {name!r} not registered"
        )
    disabled = _load_disabled(bus)
    if body.enabled:
        disabled.discard(name)
    else:
        disabled.add(name)
    _save_disabled(bus, disabled)
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
    bus: BusDep,
) -> SkillBodyOut:
    """Return the SKILL.md markdown body for ``name``."""
    if not _NAME_RE.match(name):
        raise MagiHTTPException(
            status_code=400, code="validation.skill_name", detail="invalid skill name"
        )
    try:
        body = _skills_book(bus).read_body(name)
    except SkillNotFound:
        raise MagiHTTPException(
            status_code=404, code="not_found.skill", detail=f"skill {name!r} not registered"
        ) from None
    except SkillBookError as exc:
        logger.warning("get_skill_body: read failed: %s", exc)
        raise MagiHTTPException(
            status_code=500, code="skill.read_failed", detail="read failed"
        ) from exc
    return SkillBodyOut(
        name=name, content=body.content, modified_at=body.mtime, truncated=body.truncated
    )
