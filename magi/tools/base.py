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

import functools
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Awaitable, Callable

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

    ``worker_id`` is the
    :attr:`~magi.tools.worker.ToolsWorker.worker_id` of the
    process currently executing the tool — available for tools
    that own per-process state and need to identify their own
    work across restarts.  Empty string when no worker is wired
    (tests / boot probes).

    ``bus`` is ``None`` for tests / boot probes — tools that
    require bus access should fail closed when ``ctx.bus``
    is missing.
    """

    workspace: str
    uid: int
    channel: str
    session_id: str = ""
    bus: "NewBus | None" = None
    worker_id: str = ""


#: Truncation budget for :meth:`ToolResult.ok`. Mirrors the worker's
#: own cut in ``magi.tools.worker._to_result`` (``content[:8000]``) —
#: the worker truncates unconditionally to fit the column, so a
#: payload that overflows would be cut anyway, just silently. Cutting
#: here instead lets us append an explicit marker so the model knows
#: it's looking at a partial result rather than the whole list.
_MAX_CONTENT = 8 * 1024


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
    def ok(cls, payload: Any) -> "ToolResult":
        """Success carrying a JSON-serialised ``payload``.

        ``payload`` is rendered with ``indent=2`` and
        ``ensure_ascii=False`` — the model reads this text, so
        CJK stays legible rather than escaping to ``\\uXXXX``.
        Output over :data:`_MAX_CONTENT` is cut with a visible
        ``…(truncated)`` marker.
        """
        body = json.dumps(payload, indent=2, ensure_ascii=False)
        if len(body) > _MAX_CONTENT:
            body = body[:_MAX_CONTENT] + "\n…(truncated)"
        return cls(content=body, is_error=False)

    @classmethod
    def err(cls, msg: str) -> "ToolResult":
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
    then implement :meth:`run`. Override :meth:`gate` when
    the default role check isn't enough (e.g. contact-
    ownership on top of role).

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

    #: Roles permitted to invoke this tool.
    #:
    #: Empty set (the default) means "no role-based gating" —
    #: every operator can invoke the tool regardless of role.
    #: Setting a non-empty set causes :meth:`gate` to refuse
    #: callers whose role isn't in the set.
    #:
    #: ``"admin"`` is a *virtual* role: it never appears in
    #: :attr:`Contact.role` (the role enum is just
    #: ``assigned`` / ``guest``), but :meth:`gate` treats it
    #: as a synonym for "the caller is an admin of at least
    #: one MAGIS per :class:`~magi.new_bus.library.magis.magisBook.MagisAdminBook`".
    #: That lets tools declare ``ALLOWED_ROLES = {"admin",
    #: "assigned"}`` without carrying a parallel ``admin``
    #: boolean on the contact row. A user can be both
    #: ``role='assigned'`` and a MAGIS admin — both branches
    #: pass independently.
    ALLOWED_ROLES: frozenset[str] = frozenset()

    @staticmethod
    def require_bus(
        method: Callable[..., Awaitable[ToolResult]],
    ) -> Callable[..., Awaitable[ToolResult]]:
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
                    content=(
                        "tool context has no bus; the caller "
                        "side has not migrated to new_bus"
                    ),
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

    def gate(self, ctx: ToolContext) -> str | None:
        """Runtime authorization check — called by the worker
        before :meth:`run`.

        Returns ``None`` when the caller is permitted, or an
        error message string when the caller should be denied.
        The worker turns a non-``None`` return into
        ``RunToolResult(is_error=True, ...)``.

        Default implementation builds the caller's
        **effective role-tag set**:

        - ``ctx.bus.contacts_book.get(contact_id=...)`` →
          the MAGI-local role (``assigned`` / ``guest`` /
          ``contact``) — lives on ``Contact.role``.
        - ``ctx.bus.magis_admins_book.is_admin_for(uid=...)``
          → ``True`` iff the caller has at least one row in
          the ``magis_admins`` table. Admin is a **MAGIS-level**
          concept; it never appears as a value on
          ``Contact.role`` (the role enum is just
          ``assigned`` / ``guest``). A user can be both
          ``assigned`` here and ``admin`` in some MAGIS —
          the two flags are orthogonal.

        The caller passes when their effective role-tag set
        intersects :attr:`ALLOWED_ROLES`. So a tool with
        ``ALLOWED_ROLES = frozenset({"admin", "assigned"})``
        admits both the served user and any MAGIS admin;
        a tool with ``ALLOWED_ROLES = frozenset({"admin"})``
        admits only MAGIS admins.

        Re-resolving on every call (no caching on the
        context) means a role or admin flip in the database
        takes effect on the very next tool call without a
        process restart.

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

        # Build effective role-tag set: local Contact.role
        # ∪ { "admin" } when the caller has any MAGIS admin row.
        contact = ctx.bus.contacts_book.get(contact_id=ct_id)
        if contact is None:
            return f"contact {ct_id!r} not found"
        effective: set[str] = {contact.role}
        admins_book = getattr(ctx.bus, "magis_admins_book", None)
        if admins_book is not None and admins_book.is_admin_for(uid=ct_id):
            effective.add("admin")

        if effective.isdisjoint(self.ALLOWED_ROLES):
            return (
                f"role(s) {sorted(effective)!r} is not permitted for "
                f"tool {self.name!r} "
                f"(allowed: {', '.join(sorted(self.ALLOWED_ROLES))})"
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