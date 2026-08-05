"""LLM provider layer — abstracts the upstream chat API.

The runtime speaks one interface (``LLMProvider``) regardless of
which vendor actually serves the request. v0 ships four
concrete implementations:

  - :class:`magi.providers.claude_code.ClaudeProvider` — Anthropic's
    first-party API.
  - :class:`magi.providers.minimax.MinimaxProvider` — Minimax's
    two regions (China + Global).
  - :class:`magi.providers.openai.OpenAIProvider` — OpenAI's
    official chat-completions endpoint.

The Claude + Minimax pair subclass
:class:`magi.providers.anthropic.AnthropicProvider`, which
centralises the SDK call, error mapping, and response walking.
OpenAI is on a different wire format and subclasses
:class:`LLMProvider` directly. The factory in
:mod:`magi.providers.factory` is the single source of truth for
which provider id maps to which class.

Public surface re-exported here so callers don't need to know
which submodule a class lives in::

    from magi.providers import (
        LLMProvider, ChatMessage, ChatResult,
        LLMError, LLMAuthError, LLMNetworkError,
        get_provider,
    )
"""

from magi.bus.protocols.llm_jobs import LLMJob, LLMJobResult
from magi.providers.errors import (
    LLMAuthError,
    LLMContextLengthError,
    LLMError,
    LLMNetworkError,
    LLMNotConfiguredError,
    LLMRateLimitError,
)
from magi.providers.factory import get_provider, is_known_provider, known_providers
from magi.providers.provider import (
    ChatMessage,
    ChatResult,
    LLMProvider,
    LLMStreamEvent,
)
from magi.providers.tokens import estimate_messages_tokens, estimate_string_tokens

__all__ = [
    "LLMProvider",
    "ChatMessage",
    "ChatResult",
    "LLMStreamEvent",
    "LLMError",
    "LLMAuthError",
    "LLMRateLimitError",
    "LLMNetworkError",
    "LLMContextLengthError",
    "LLMNotConfiguredError",
    "get_provider",
    "is_known_provider",
    "known_providers",
    "LLMJob",
    "LLMJobResult",
    "estimate_messages_tokens",
    "estimate_string_tokens",
]
