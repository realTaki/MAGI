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