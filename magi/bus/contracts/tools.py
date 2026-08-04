"""Tool-side public DTOs exchanged across the bus.

Tool definitions are durable, role-filtered, revision-tracked
records on the bus side; the executable ``Tool`` class lives in
:mod:`magi.tools.registry` for worker dispatch only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ToolClaim:
    """A leased tool job returned to a tools-owned worker."""

    job_id: str
    run_id: str
    tool_call_id: str
    tool_name: str
    payload: dict[str, Any]
    attempts: int
    source: str | None = None
    catalog_revision: int | None = None
    schema_hash: str | None = None


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    """The durable, LLM-visible definition of one executable tool.

    This is deliberately data rather than a ``Tool`` object.  It
    can cross between the worker, actor and HTTP handlers without
    exposing a registry, ORM object, or callable implementation.
    """

    name: str
    source: str
    description: str
    input_schema: dict[str, Any]
    allowed_roles: tuple[str, ...] = ()
    enabled: bool = True
    implementation_version: str | None = None
    schema_hash: str = ""
    revision: int = 0


@dataclass(frozen=True, slots=True)
class ToolCatalogSnapshot:
    """Observable state returned after an atomic catalog replacement."""

    revision: int
    snapshot_hash: str
    definitions: tuple[ToolDefinition, ...]


@dataclass(frozen=True, slots=True)
class ToolContext:
    """JSON-safe execution context supplied to a tool worker.

    The runtime's physical filesystem location (``state_dir``) is
    owned by the BUS and **not** exposed here — tools that need
    to read or write persistent state call the public
    ``bus.<service>`` methods rather than handling the path
    themselves. Only the user-facing ``workspace`` (the operator's
    ``/workspace`` mount) is part of the tool context, because it
    is the boundary tools operate against (``safe_resolve`` etc.).
    """

    workspace: str
    uid: int
    channel: str
    session_id: str = ""


@dataclass(frozen=True, slots=True)
class ToolResult:
    """A provider-valid result emitted by a tool worker."""

    content: str
    is_error: bool = False


__all__ = [
    "ToolClaim",
    "ToolDefinition",
    "ToolCatalogSnapshot",
    "ToolContext",
    "ToolResult",
]
