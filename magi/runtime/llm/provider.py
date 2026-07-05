"""Abstract LLM provider + the wire-shape types it speaks.

The runtime + agent loop only ever see ``LLMProvider`` and the
``ChatMessage`` / ``ChatResult`` dataclasses. New vendors plug
in by subclassing ``LLMProvider`` and registering themselves
in :mod:`magi.runtime.llm.factory`.

Design notes:

- The provider's ``chat()`` is async because the FastAPI event
  loop is async, and a single round-trip is the unit of work
  every channel waits on. Implementations that hit a sync SDK
  (like the official ``anthropic`` package) wrap the call in
  ``asyncio.to_thread`` so the event loop stays free.

- ``ChatResult.text`` is the user-facing reply. ``thinking`` is
  the chain-of-thought block (if the model emits one). The
  default is to never send ``thinking`` to the user, but keep
  it in audit so debugging "why did the model say that" is one
  query away.

- ``raw_blocks`` holds the full content-block dump for audit.
  Kept on the result so the agent can persist it without
  re-walking the upstream response.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Literal


MessageRole = Literal["user", "assistant"]


@dataclass(frozen=True)
class ChatMessage:
    """One turn of a chat.

    ``role`` is the speaker; ``content`` is the plain text.

    ``content_blocks`` (D.16) carries a list of structured
    blocks for the cases where text alone isn't enough:

    - ``user`` messages with ``tool_result`` blocks (sent back
      to the API after a tool ran). When ``content_blocks`` is
      set the SDK uses it; ``content`` becomes the optional
      accompanying text ("here are the results").
    - Assistant ``raw_blocks`` replay (so a prior turn's
      ``tool_use`` / ``thinking`` blocks round-trip back to
      the API verbatim — Anthropic requires this; an
      assistant message that only sends text loses its
      ``tool_use`` ID and the next tool_result can't bind
      back to it).

    The provider implementation serialises these into the
    Anthropic SDK's expected ``{role, content: str |
    list[Block]}`` shape. v0 only knows ``tool_result`` and
    the raw-block replay case; image / document blocks
    aren't on the roadmap yet.
    """

    role: MessageRole
    content: str
    content_blocks: list[dict] | None = None


@dataclass
class ChatResult:
    """A single chat completion.

    Attributes
    ----------
    text
        The user-facing reply. If the model emitted only
        thinking blocks (no text), this falls back to
        ``"(empty reply)"`` so downstream code never has to
        special-case the empty case.
    thinking
        Chain-of-thought from the model, if any. Audit-only;
        never send to the user unless a debug mode asks for it.
    model
        The model identifier the upstream actually used (may
        differ from the requested one if the provider aliased).
    usage
        Token usage as a dict (``input_tokens`` /
        ``output_tokens`` / ``cache_read_input_tokens`` etc.).
        ``None`` if the upstream didn't report.
    raw_blocks
        Full per-block dump (text, thinking, tool_use) for the
        audit row. The runtime writes this verbatim so a future
        "replay" tool can rebuild the conversation exactly.
    stop_reason
        The Anthropic ``stop_reason`` string:
        ``"end_turn"`` (model produced text, agent can stop
        looping), ``"tool_use"`` (model wants tools to run),
        ``"max_tokens"`` / ``"stop_sequence"`` (terminal
        edge cases). v0 only branches on the first two.
    tool_uses
        List of ``{"id", "name", "input"}`` blocks the
        provider extracted from the response. Empty list
        when the model didn't call any tools.
    """

    text: str
    thinking: str | None = None
    model: str = ""
    usage: dict | None = None
    raw_blocks: list[dict] = field(default_factory=list)
    stop_reason: str | None = None
    tool_uses: list[dict] = field(default_factory=list)


class LLMProvider(ABC):
    """Abstract base for every chat-completion provider.

    Subclasses must set:

    - ``name`` (class attr or instance attr): the canonical
      provider id as it appears in ``Employee.provider`` and
      the audit log. Lowercase, hyphenated.
    - ``default_model()``: the fallback model name when the
      caller doesn't pass one explicitly.

    The constructor signature is deliberately permissive
    (``api_key``, optional ``model``) so a future
    AnthropicProvider / OpenAIProvider can take extra kwargs
    (``base_url``, ``organization``) without churn.
    """

    #: Canonical provider id, e.g. ``"minimax-cn"``.
    name: str = ""

    def __init__(self, api_key: str, model: str | None = None) -> None:
        if not api_key:
            raise ValueError("api_key is required")
        self.api_key = api_key
        self.model = model or self.default_model()

    @abstractmethod
    def default_model(self) -> str:
        """The default model id when the caller didn't specify one."""

    @abstractmethod
    async def chat(
        self,
        system: str | None,
        messages: list[ChatMessage],
        max_tokens: int = 1024,
        tools: list[dict] | None = None,
    ) -> ChatResult:
        """One chat turn.

        ``system`` is the system prompt (``None`` for empty).
        ``messages`` is the running history in order; v0 always
        sends exactly one user message but the interface takes
        a list so a future checkpoint can pass prior turns.
        ``max_tokens`` is the upstream cap; defaults to 1024
        which is a safe middle for chat replies.
        ``tools`` (D.16) is the Anthropic-shape list of tool
        schemas the LLM may call this turn. ``None`` / ``[]``
        means "no tools available" — the model falls back to
        a plain-text reply.
        """
