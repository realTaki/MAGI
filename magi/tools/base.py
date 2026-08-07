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
  - ``gate(ctx)``   — in-run authorization (default: role
                      check via ``ctx.bus.contacts_book``)
  - ``run(ctx, **kwargs)`` — actually execute

Execution-facing DTOs — :class:`ToolContext` (what the
worker hands the tool) and :class:`ToolResult` (what the
tool returns) — live in this module because they're part
of the ``Tool`` abstraction itself, not a bus concept.
LLM-contract DTOs (``ToolDefinition`` / ``ToolCatalogSnapshot``)
live in :mod:`magi.new_bus.library.local.toolsBook` next to
the Books that publish them. Job-side DTOs (``RunToolJob`` /
``RunToolResult``) live in
:mod:`magi.new_bus.guild.runToolJob`.

Each tool implementation lives in its own module under
``magi/tools/`` and exports a single class.
``registry.get_tool()`` is the lazy-import entry point so
test isolation works (a test can monkeypatch one tool
without importing the whole batch).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from magi.new_bus import NewBus


# -- execution I/O DTOs ---------------------------------------------------
#
# These describe the contract between the worker (caller) and the
# executable Tool class. They are NOT bus concepts — they live here
# with the Tool abstraction they describe.


@dataclass(frozen=True, slots=True)
class ToolContext:
    """JSON-safe execution context supplied to a tool worker.

    The runtime's ``state_dir`` is owned by new_bus and **not**
    exposed here — tools that need persistent state call the
    bus books rather than handling paths themselves. Only the
    user-facing ``workspace`` (resolved from
    ``MAGI_WORKSPACE_DIR``) is part of the tool context, because
    it's the boundary tools operate against (``safe_resolve``
    etc.).

    ``bus`` is the new_bus facade the worker is attached to.
    Tools that need to read/write persistent state reach for
    ``ctx.bus.<book>.X(...)`` instead of holding their own
    reference. Role gating is handled centrally by
    :meth:`Tool.gate` (reads ``ctx.bus.contacts_book``); tools
    don't carry caller-role fields on the context — they let
    ``gate()`` re-resolve fresh on every call so role flips
    take effect without a process restart.

    ``bus`` is ``None`` for tests / boot probes — tools that
    require bus access should fail closed when ``ctx.bus``
    is missing.
    """

    workspace: str
    uid: int
    channel: str
    session_id: str = ""
    bus: "NewBus | None" = None


@dataclass(frozen=True, slots=True)
class ToolResult:
    """A provider-valid result emitted by a tool worker.

    Tools should never raise to surface "expected failure" —
    wrap the failure in :class:`ToolResult` with ``is_error=True``
    so the worker's bookkeeping stays uniform. Real bugs raise;
    the worker catches and translates them.
    """

    content: str
    is_error: bool = False


__all__ = ["Tool", "ToolContext", "ToolResult"]


class Tool(ABC):
    """One callable the LLM can request.

    Subclass and set ``name`` / ``description`` /
    ``input_schema`` / ``ALLOWED_ROLES`` as class attributes,
    then implement :meth:`run`. Override :meth:`gate` when
    the default role check isn't enough (e.g. contact-ownership
    on top of role).
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

    #: Roles permitted to invoke this tool.
    #:
    #: Empty set (the default) means "no role-based gating" —
    #: every operator can invoke the tool regardless of role.
    #: Setting a non-empty set causes :meth:`gate` to refuse
    #: callers whose role isn't in the set.
    ALLOWED_ROLES: frozenset[str] = frozenset()

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
        """

    def gate(self, ctx: ToolContext) -> str | None:
        """Runtime authorization check — called by the worker
        before :meth:`run`.

        Returns ``None`` when the caller is permitted, or an
        error message string when the caller should be denied.
        The worker turns a non-``None`` return into
        ``RunToolResult(is_error=True, ...)``.

        Default implementation: looks up the caller's role
        via :attr:`ctx.bus.contacts_book` and checks it
        against :attr:`ALLOWED_ROLES`. Re-resolving on
        every call (no caching on the context) means a role
        flip in the database takes effect on the very next
        tool call without a process restart.

        Override for tools that need additional checks on
        top of the role (e.g. ``UpdateContactNoteTool`` adds
        "can only edit your own contact"). Always call
        ``super().gate(ctx)`` first inside the override so
        the role check stays in one place.
        """
        if not self.ALLOWED_ROLES:
            return None  # no gate configured — every caller passes

        try:
            ct_id = int(ctx.uid)
        except (TypeError, ValueError):
            return f"uid {ctx.uid!r} is not a valid id"
        if ct_id == 0:
            # The chat / TG handlers always set a real id;
            # ``0`` is the loop's placeholder for "no caller
            # resolved yet". Refuse rather than silently
            # letting an unset-context caller through.
            return (
                "tool requires a known uid (got 0); "
                "caller did not authenticate through a "
                "cookie / TG binding."
            )
        if ctx.bus is None:
            # Old-bus ToolContext (MCP-side callers until
            # MCP migrates) — no role resolution is possible.
            return (
                "role check unavailable: tool context has no bus; "
                "the caller side has not migrated to new_bus"
            )

        contact = ctx.bus.contacts_book.get(contact_id=ct_id)
        if contact is None:
            return f"contact {ct_id!r} not found"
        if contact.role not in self.ALLOWED_ROLES:
            return (
                f"role {contact.role!r} is not permitted for this "
                f"tool (allowed: {', '.join(sorted(self.ALLOWED_ROLES))})"
            )
        return None

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