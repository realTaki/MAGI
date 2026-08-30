"""Executable tool: a name the LLM can call.

Catalog rows live on BUS (``Tool`` the record). This module is the
in-process class the tools Worker dispatches to.

``run(**arguments)`` receives only the Job's arguments (plus a few
per-call fields such as ``conversation_id``). Tools that need the
Runtime (Jobs, workspace path) take ``bus`` at construction.
"""

from __future__ import annotations

import functools
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class ToolResult:
    """What ``run`` returns to the Worker. Mapped onto ``RunToolResult``.

    Expected failure is ``is_error=True``, not an exception. Real bugs raise.
    """

    content: str
    is_error: bool = False

    @classmethod
    def ok(cls, payload: Any) -> ToolResult:
        body = json.dumps(payload, indent=2, ensure_ascii=False, default=_tool_json_default)
        if len(body) > _MAX_CONTENT:
            body = body[:_MAX_CONTENT] + "\n…(truncated)"
        return cls(content=body, is_error=False)

    @classmethod
    def err(cls, msg: str) -> ToolResult:
        return cls(content=msg, is_error=True)


_MAX_CONTENT = 8 * 1024


def _tool_json_default(value: object) -> object:
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


class BaseTool(ABC):
    """One callable the LLM can request.

    Set ``name`` / ``description`` / ``input_schema`` / ``ALLOWED_ROLES``.
    ``ALLOWED_ROLES`` is catalog metadata; it is not checked at run.

    Tools that touch the Runtime take ``bus`` at construction. Workspace
    is ``bus.workspace`` — it is not a separate argument. ``run`` only
    sees Job arguments.
    """

    name: str = ""
    description: str = ""
    input_schema: dict[str, Any] = {}
    ALLOWED_ROLES: frozenset[str] = frozenset()

    def __init__(self, *, bus: Any = None) -> None:
        self.bus = bus

    @staticmethod
    def require_bus(method):
        """Fail closed when this instance was constructed without a bus."""

        @functools.wraps(method)
        async def wrapper(self: BaseTool, **kwargs: Any) -> ToolResult:
            if self.bus is None:
                return ToolResult.err("tool was constructed without a bus")
            return await method(self, **kwargs)

        return wrapper

    @abstractmethod
    async def run(self, **kwargs: Any) -> ToolResult:
        """Execute using Job arguments."""

    def to_anthropic_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


__all__ = ["BaseTool", "ToolResult"]
