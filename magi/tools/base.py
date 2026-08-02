
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

``ToolContext`` and ``ToolResult`` moved to :mod:`magi.types`
so agent, tools, and other packages can all import them
without pulling in the tools package.

Each tool implementation lives in its own module under
``magi/tools/`` and exports a single class.
``registry.get_tools()`` is the lazy-import entry point so
test isolation works (a test can monkeypatch one tool
without importing the whole batch).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

# Re-export from the shared types module so existing
# ``from magi.tools.base import ToolContext, ToolResult``
# imports keep working during the transition.
from magi.db.types import ToolContext, ToolResult  # noqa: F401


class Tool(ABC):
    """One callable the LLM can request.

    Subclass and set ``name`` / ``description`` /
    ``input_schema`` as class attributes, then implement
    ``run``. The agent loop fetches all registered tools
    once per chat and passes their schemas to the LLM.
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

    #: Roles permitted to see this tool in their tool menu.
    #:
    #: Empty set (the default) means "no role-based gating" —
    #: every operator sees the tool regardless of role.
    #: Setting a non-empty set causes
    #: :meth:`is_allowed_for_role` to filter the tool out of
    #: the menu for any operator whose role isn't in the set,
    #: so the model never learns the tool exists when it
    #: can't be invoked.
    #:
    #: Role-gated tools should still defensively re-check
    #: inside :meth:`run` (the registry filter assumes the
    #: call site passes ``caller_role`` through — a future
    #: caller that bypasses :func:`registry.get_tools` could
    #: otherwise expose the tool to anyone).
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
            ``ValueError`` on bad input; the loop catches
            and returns ``is_error=True`` to the LLM)
          - return a :class:`ToolResult`
          - never raise to surface "expected failure" —
            wrap in ``ToolResult(is_error=True, ...)`` so
            the loop's bookkeeping is uniform
        """

    def is_allowed_for_role(
        self,
        role: str | None,
        admin: bool = False,
    ) -> bool:
        """Whether the caller should see this tool in the menu.

        ``role=None`` means "caller didn't supply a role" —
        typically a test or a boot-time probe. v0 defaults
        to permissive (the caller sees the tool), matching
        the historic behaviour of :func:`registry.get_tools`
        before role filtering landed. The production path
        in :func:`magi.agent.loop.handle_message` always
        passes an explicit ``caller_role`` (resolved from
        the operator's ``Contact.role``), so an unfiltered
        ``None`` call from production would itself be a bug
        — and the right fix for that bug is to wire the
        caller_role through, not to add a layer of refusal
        that hides the tool from legitimate test code.

        ``admin=True`` is a separate opt-in that grants
        access to operators who are WebUI-signed-in but
        aren't on the served user's role list. After the
        2024 role/admin split, ``admin`` lives on its own
        boolean column (``Contact.admin``); ``role='admin'``
        is no longer reachable from the enum. Tools that
        previously keyed ``ALLOWED_ROLES`` on
        ``{"admin", "assigned"}`` now use an empty
        ``ALLOWED_ROLES`` and rely on the in-run gate.
        """
        if admin:
            # WebUI operator — bypasses the role-enum
            # filter. Matches the API's
            # ``_enforce_creator_can_create`` semantic.
            return True
        if not self.ALLOWED_ROLES:
            # No restrictions declared: any caller, including
            # the ``role=None`` test / boot path, sees the tool.
            return True
        if role is None:
            # ``ALLOWED_ROLES`` is set but we don't know who
            # the caller is — show the tool rather than
            # hiding it from probe / test contexts. Real
            # gate enforcement comes from the explicit
            # ``caller_role`` plumbing; this branch only
            # kicks in when that plumbing is missing.
            return True
        return role in self.ALLOWED_ROLES

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


def caller_role_denied_reason(
    ctx: "ToolContext",
    allowed_roles: "frozenset[str] | set[str]",
) -> str | None:
    """Return an error message if ``ctx``'s caller isn't in
    ``allowed_roles``; ``None`` if the caller is permitted.

    Used inside a tool's :meth:`Tool.run` as the second-
    layer defence — the registry's role filter at
    :func:`magi.tools.registry.get_tools` is the
    first gate and strips this tool out of the LLM's
    menu for any caller whose role isn't in
    ``allowed_roles``. The check here ensures a caller
    that bypasses the registry (test code, a future
    entry point that forgets the filter) still fails
    closed with a friendly ``is_error=True``.

    Resolves the caller's role via a fresh Contact
    lookup each call so role flips (``assigned`` →
    ``contact`` mid-conversation) take effect on the
    very next tool call without a process restart.

    The SQLAlchemy / Contact imports are local — the
    agent loop imports this module without paying for
    the DB stack at module load.
    """
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
    from magi.db import Contact, open_session
    with open_session() as db:
        contact = db.get(Contact, ct_id)
    if contact is None:
        return f"contact {ct_id!r} not found"
    if contact.role not in allowed_roles:
        return (
            f"role {contact.role!r} is not permitted for this "
            f"tool (allowed: {', '.join(sorted(allowed_roles))})"
        )
    return None
