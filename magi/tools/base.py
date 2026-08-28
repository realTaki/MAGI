"""Tool base class.

A :class:`Tool` is a callable the LLM can ask the agent
loop to run. v0 ships four (see ``registry.py``); future
skills (D.17) are also tools under the hood — they just
get registered from a config file instead of being
hard-coded.

The protocol is intentionally tiny:

  - ``name``        — what the model calls it by
  - ``description`` — what the model reads to decide when
                      to call it
  - ``input_schema`` — JSON Schema dict (Anthropic wants
                      it; we don't validate it ourselves —
                      the model emits the input)
  - ``run(ctx, **kwargs)`` — actually execute

Role visibility lives on the catalog (agent menu /
``ALLOWED_ROLES``), not on the execution path.

Execution-facing DTOs — :class:`ToolContext` (what the
worker hands the tool) and :class:`ToolResult` (what the
tool returns) — live in this module because they're part
of the ``Tool`` abstraction itself, not a bus concept.
LLM-contract DTOs (``ToolDefinition`` / ``ToolCatalogSnapshot``)
live in :mod:`magi.bus.firmwares.books.local.toolsBook` next to
the Books that publish them. Job-side DTOs (``RunToolJob`` /
``RunToolResult``) live in
:mod:`magi.bus.firmwares.jobs.runToolJob`.

Each tool implementation lives in its own module under
``magi/tools/`` and exports a single class.
``registry.get_tool()`` is the lazy-import entry point so
test isolation works (a test can monkeypatch one tool
without importing the whole batch).
"""

from __future__ import annotations

import functools
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from magi.bus import Bus


# -- execution I/O DTOs ---------------------------------------------------
#
# These describe the contract between the worker (caller) and the
# executable Tool class. They are NOT bus concepts — they live here
# with the Tool abstraction they describe.


@dataclass(frozen=True, slots=True)
class ToolContext:
    """JSON-safe execution context supplied to a tool worker.

    The runtime's ``state_dir`` is owned by bus and **not**
    exposed here — tools that need persistent state call the
    bus books rather than handling paths themselves. Only the
    user-facing ``workspace`` (resolved from
    ``HOST_WORKSPACE_DIR`` + ``MAGI_NAME`` via
    :func:`magi.startup.paths.resolve_workspace_dir`) is part of
    the tool context, because it's the boundary tools operate
    against (``safe_resolve`` etc.).

    ``bus`` is the bus facade the worker is attached to.
    Tools that need to read/write persistent state reach for
    ``ctx.bus.<book>.X(...)`` instead of holding their own
    reference.

    ``bus`` is ``None`` for tests / boot probes — tools that
    require bus access should fail closed when ``ctx.bus``
    is missing.
    """

    workspace: str
    contact_id: int
    channel: str
    conversation_id: int = 0  # chat_conversations.id
    bus: Bus | None = None


#: Truncation budget for :meth:`ToolResult.ok`. Mirrors the worker's
#: own cut in ``magi.tools.worker._to_result`` (``content[:8000]``) —
#: the worker truncates unconditionally to fit the column, so a
#: payload that overflows would be cut anyway, just silently. Cutting
#: here instead lets us append an explicit marker so the model knows
#: it's looking at a partial result rather than the whole list.
_MAX_CONTENT = 8 * 1024


def _tool_json_default(value: object) -> object:
    """Encode transport-only values at the tool-result boundary.

    Books retain native ``datetime`` values; the LLM tool envelope is text,
    so this is the one central place that turns them into JSON scalars.
    """

    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


@dataclass(frozen=True, slots=True)
class ToolResult:
    """A provider-valid result emitted by a tool worker.

    Tools should never raise to surface "expected failure" —
    wrap the failure in :class:`ToolResult` with ``is_error=True``
    so the worker's bookkeeping stays uniform. Real bugs raise;
    the worker catches and translates them.

    :meth:`ok` / :meth:`err` are the constructors for the common
    case (a JSON payload / an error string). Tools returning plain
    prose still construct ``ToolResult(content=...)`` directly.
    """

    content: str
    is_error: bool = False

    @classmethod
    def ok(cls, payload: Any) -> ToolResult:
        """Success carrying a JSON-serialised ``payload``.

        ``payload`` is rendered with ``indent=2`` and
        ``ensure_ascii=False`` — the model reads this text, so
        CJK stays legible rather than escaping to ``\\uXXXX``.
        Output over :data:`_MAX_CONTENT` is cut with a visible
        ``…(truncated)`` marker.
        """
        body = json.dumps(payload, indent=2, ensure_ascii=False, default=_tool_json_default)
        if len(body) > _MAX_CONTENT:
            body = body[:_MAX_CONTENT] + "\n…(truncated)"
        return cls(content=body, is_error=False)

    @classmethod
    def err(cls, msg: str) -> ToolResult:
        """Expected failure carrying an operator-readable ``msg``.

        Not for bugs — those raise and the worker translates them
        into a ``tool.crashed`` envelope.
        """
        return cls(content=msg, is_error=True)


__all__ = ["Tool", "ToolContext", "ToolResult"]


class Tool(ABC):
    """One callable the LLM can request.

    Subclass and set ``name`` / ``description`` /
    ``input_schema`` / ``ALLOWED_ROLES`` as class attributes,
    then implement :meth:`run`. ``ALLOWED_ROLES`` is catalog
    metadata for the agent menu; it is not checked at run.

    Tools that touch the bus (``ctx.bus.<book>.X(...)``)
    should decorate their :meth:`run` with
    :meth:`Tool.require_bus` (a ``@staticmethod`` living
    on this base class) to opt into the ``ctx.bus is
    None`` failure-closed path. Tools that only need
    ``ctx.workspace`` (filesystem, shell, etc.) **don't**
    decorate — they keep running with ``bus=None`` in
    tests and boot probes.

    Keeping the decorator on the base class means tool
    files don't grow a new ``from magi.tools.base import
    ..., require_bus`` line — :class:`Tool` is already
    imported by every concrete tool, so
    ``@Tool.require_bus`` just works.
    """

    #: The name the LLM uses to invoke this tool. Must
    #: match the regex Anthropic accepts — lowercase
    #: letters, digits, underscores; max 64 chars.
    name: str = ""

    #: Free-text description shown to the model. Be
    #: specific about what the tool does and when to use
    #: it; vague descriptions lead the model to misuse
    #: the tool.
    description: str = ""

    #: JSON Schema dict for the tool's input. The LLM
    #: generates input matching this shape; we don't
    #: validate it (Anthropic rejects malformed input
    #: upstream before the request even leaves).
    input_schema: dict[str, Any] = {}

    #: Roles that may see this tool in the agent catalog.
    #: Empty means unrestricted. The worker does not re-check
    #: this at execution — the menu filter is the gate.
    ALLOWED_ROLES: frozenset[str] = frozenset()

    @staticmethod
    def require_bus(method):
        """Decorate :meth:`run` to fail closed when
        ``ctx.bus`` is missing.

        Usage::

            class AddActionItemTool(Tool):
                @Tool.require_bus
                async def run(self, ctx, **kwargs):
                    ...

        Lives on :class:`Tool` so concrete tool files
        don't grow a new import line — ``@Tool.require_bus``
        is enough. Opt-in: tools that don't touch the bus
        (filesystem ops, shell tools) leave ``run``
        undecorated and run with ``bus=None`` in tests.

        Type-checker note: the wrapper's signature is
        ``(self, ctx, **kwargs)``, matching :meth:`Tool.run`
        shape so Liskov holds. The wrapper is an
        ``async def``; the abstract base's inferred return
        is also ``Coroutine[..., ..., ToolResult]``, so
        letting inference do the talking on the wrapper
        side keeps both ends of the override aligned.
        """

        @functools.wraps(method)
        async def wrapper(
            self: Any,
            ctx: ToolContext,
            **kwargs: Any,
        ) -> ToolResult:
            if ctx.bus is None:
                return ToolResult(
                    content=("tool context has no bus; the caller side has not migrated to bus"),
                    is_error=True,
                )
            return await method(self, ctx, **kwargs)

        return wrapper

    @abstractmethod
    async def run(
        self,
        ctx: ToolContext,
        **kwargs: Any,
    ) -> ToolResult:
        """Execute the tool.

        ``kwargs`` are the fields declared in
        ``input_schema``. Tools should:
          - validate ``kwargs`` themselves (raise
            ``ValueError`` on bad input; the worker catches
            and returns ``is_error=True`` to the LLM)
          - return a :class:`ToolResult`
          - never raise to surface "expected failure" —
            wrap in ``ToolResult(is_error=True, ...)`` so
            the loop's bookkeeping is uniform

        Tools that touch ``ctx.bus.<book>`` should
        decorate this method with :func:`require_bus` —
        see the Tool class docstring for the opt-in
        contract.
        """

    def to_anthropic_schema(self) -> dict[str, Any]:
        """Render this tool's metadata into the dict shape
        the Anthropic SDK expects.

        The shape is documented at
        https://docs.anthropic.com/en/docs/build-with-claude/tool-use
        — ``name``, ``description``, ``input_schema``.
        """
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }
