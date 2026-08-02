"""Tool schema read/write helpers for the ``tools`` table.

The tool worker is the sole writer — it upserts every known tool's
LLM-facing metadata on startup.  The agent (and WebUI API) are
readers — they query enabled rows, optionally filtered by caller role.

This module deliberately lives in :mod:`magi.db` so both the agent
and the tools worker can import it without either pulling in the
other's domain package.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select

from magi.db.base import utcnow_naive
from magi.db.engine import open_session
from magi.db.models_tool import ToolRegistry

logger = logging.getLogger("magi.db.tool_schemas")


# -- seed (writer — called by the tool worker) ----------------------------

def _empty_allowed_roles(roles: frozenset | None) -> list | None:
    """Normalise a possibly-empty frozenset into the DB-friendly form."""
    if roles is None or len(roles) == 0:
        return None
    return list(roles)


def upsert_tool_schema(
    *,
    name: str,
    description: str,
    input_schema: dict,
    allowed_roles: frozenset | None = None,
    source: str = "builtin",
    enabled: bool = True,
    priority: int = 0,
    state_dir: str | None = None,
) -> bool:
    """Insert or refresh one tool row.  Returns ``True`` on insert, ``False`` on update."""
    now = utcnow_naive()
    with open_session(state_dir) as db:
        existing = db.get(ToolRegistry, name)
        if existing is not None:
            existing.description = description
            existing.input_schema = input_schema
            existing.allowed_roles = _empty_allowed_roles(allowed_roles)
            existing.source = source
            existing.enabled = enabled
            existing.priority = priority
            existing.updated_at = now
            db.commit()
            return False
        db.add(
            ToolRegistry(
                name=name,
                description=description,
                input_schema=input_schema,
                allowed_roles=_empty_allowed_roles(allowed_roles),
                source=source,
                enabled=enabled,
                priority=priority,
                created_at=now,
                updated_at=now,
            )
        )
        db.commit()
        return True


def seed_all_tool_schemas(
    tools: list[Any],  # list[Tool] — typed as Any to avoid circular import
    state_dir: str | None = None,
) -> tuple[int, int]:
    """Upsert every tool's schema into the ``tools`` table.

    Called by the tool worker on startup.  Returns ``(inserted, updated)``
    counts, useful for logging.

    The list index becomes the ``priority`` column so the LLM sees tools
    in their registration order (deterministic and deliberate — the
    registry author controls presentation by ordering the list).
    """
    inserted = updated = 0
    for idx, tool in enumerate(tools):
        schema = tool.to_anthropic_schema()
        is_new = upsert_tool_schema(
            name=tool.name,
            description=tool.description,
            input_schema=schema.get("input_schema", {}),
            allowed_roles=tool.ALLOWED_ROLES,
            source="builtin",
            priority=idx,
            state_dir=state_dir,
        )
        if is_new:
            inserted += 1
        else:
            updated += 1
    return inserted, updated


# -- read (used by the agent loop and WebUI API) --------------------------

def _role_matches(allowed_roles: list | None, role: str | None, admin: bool) -> bool:
    """Check whether ``role`` (or admin flag) is permitted by ``allowed_roles``."""
    if admin:
        return True
    if allowed_roles is None or len(allowed_roles) == 0:
        return True
    if role is None:
        return True  # no role info → permissive (matches legacy behaviour)
    return role in allowed_roles


def get_tool_schemas(
    state_dir: str | None = None,
    *,
    caller_role: str | None = None,
    caller_admin: bool = False,
    source: str | None = None,
) -> list[dict]:
    """Return Anthropic-shaped tool schemas from the DB.

    Filters by role/permission and optionally by ``source``
    (``"builtin"``, ``"mcp"``, ``"skill"``).  Returns the list in
    ``priority`` order (lower value = earlier in menu).
    """
    with open_session(state_dir) as db:
        stmt = select(ToolRegistry).where(ToolRegistry.enabled == True)  # noqa: E712
        if source is not None:
            stmt = stmt.where(ToolRegistry.source == source)
        stmt = stmt.order_by(ToolRegistry.priority, ToolRegistry.name)
        rows = db.execute(stmt).scalars().all()

    return [
        row.to_llm_schema()
        for row in rows
        if _role_matches(row.allowed_roles, caller_role, caller_admin)
    ]


def get_tools_grouped(
    state_dir: str | None = None,
    *,
    caller_role: str | None = None,
    caller_admin: bool = False,
) -> tuple[list[dict], list[dict]]:
    """Return ``(builtin_schemas, mcp_schemas)``, both role-filtered.

    Used by the WebUI Knowledge → Tools page and the agent loop.
    """
    builtin = get_tool_schemas(state_dir, caller_role=caller_role, caller_admin=caller_admin, source="builtin")
    mcp = get_tool_schemas(state_dir, caller_role=caller_role, caller_admin=caller_admin, source="mcp")
    return builtin, mcp


def get_tool_schema_by_name(
    name: str,
    state_dir: str | None = None,
    *,
    caller_role: str | None = None,
    caller_admin: bool = False,
) -> dict | None:
    """Look up a single tool's schema.  ``None`` if not found or role-denied."""
    with open_session(state_dir) as db:
        row = db.get(ToolRegistry, name)
    if row is None or not row.enabled:
        return None
    if not _role_matches(row.allowed_roles, caller_role, caller_admin):
        return None
    return row.to_llm_schema()
