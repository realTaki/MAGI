"""LLM provider layer — abstracts the upstream chat API.

The runtime speaks one interface (``LLMProvider``) regardless of
which vendor actually serves the request. v0 ships three
concrete implementations, all on the Anthropic Messages API
wire format:

  - :class:`magi.providers.claude.ClaudeProvider` — Anthropic's
    first-party API.
  - :class:`magi.providers.minimax.MinimaxProvider` — Minimax's
    two regions (China + Global).
  - Both subclass :class:`magi.providers.anthropic.AnthropicProvider`
    which centralises the SDK call, error mapping, and
    response walking.

New vendors on a different wire format (e.g. OpenAI) would
land as a new file subclassing :class:`LLMProvider` directly,
plus a one-line branch in
:func:`magi.providers.factory.get_provider`.

Public surface re-exported here so callers don't need to know
which submodule a class lives in::

    from magi.providers import (
        LLMProvider, ChatMessage, ChatResult,
        LLMError, LLMAuthError, LLMNetworkError,
        get_provider,
    )
"""

from magi.bus.protocols.provider_jobs import ProviderJob, ProviderJobResult
from magi.providers.errors import (
    LLMError,
    LLMAuthError,
    LLMRateLimitError,
    LLMNetworkError,
    LLMContextLengthError,
    LLMNotConfiguredError,
)
from magi.providers.factory import get_provider, is_known_provider, known_providers
from magi.providers.provider import (
    LLMProvider,
    ChatMessage,
    ChatResult,
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
    "ProviderJob",
    "ProviderJobResult",
    "estimate_messages_tokens",
    "estimate_string_tokens",
]
