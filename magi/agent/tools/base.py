"""Tool base class + the per-call context.

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

``ToolContext`` carries the state the tool needs to do
its work without each tool having to reach into globals.
v0 fields:
  - ``state_dir``    — ``MAGI_STATE_DIR`` value
  - ``workspace``    — the resolved workspace root
  - ``chat_id``      — the current conversation's chat id
  - ``employee_id``  — who is on the other end (for
                        audit / future per-employee limits)
  - ``channel``      — ``"webui"`` / ``"tg"`` / ``"scheduled"``

Each tool implementation lives in its own module under
``magi/agent/tools/`` and exports a single class.
``registry.get_tools()`` is the lazy-import entry point so
test isolation works (a test can monkeypatch one tool
without importing the whole batch).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ToolContext:
    """Per-call state passed to every tool.

    Frozen so a tool can't accidentally mutate the context
    mid-run (the agent loop shares one ``ToolContext``
    instance across all iterations of one chat turn).
    """

    state_dir: str
    workspace: Path
    chat_id: str
    employee_id: int
    channel: str


@dataclass
class ToolResult:
    """What a tool returns to the agent loop.

    ``content`` is what the LLM sees next turn (as a
    ``tool_result`` block). ``is_error=True`` tells the LLM
    "this didn't work, here's why; pick a different
    approach" — the loop doesn't change its behavior
    otherwise (the LLM decides what to do based on the
    content). v0 truncates ``content`` to 8 KB before
    feeding it back so a runaway shell command or 50 MB
    log file can't blow up the next LLM call.
    """

    content: str
    is_error: bool = False


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

    def is_allowed_for_role(self, role: str | None) -> bool:
        """Whether ``role`` should see this tool in the menu.

        ``role=None`` means "caller didn't supply a role" —
        typically a test or a boot-time probe. v0 defaults
        to permissive (the caller sees the tool), matching
        the historic behaviour of :func:`registry.get_tools`
        before role filtering landed. The production path
        in :func:`magi.agent.loop.handle_message` always
        passes an explicit ``caller_role`` (resolved from
        the operator's ``Employee.role``), so an unfiltered
        ``None`` call from production would itself be a bug
        — and the right fix for that bug is to wire the
        caller_role through, not to add a layer of refusal
        that hides the tool from legitimate test code.
        """
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