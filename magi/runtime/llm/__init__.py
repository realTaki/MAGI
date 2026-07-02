"""LLM provider layer — abstracts the upstream chat API.

The runtime speaks one interface (``LLMProvider``) regardless of
which vendor actually serves the request. v0 ships a single
concrete implementation, ``MinimaxProvider`` (Anthropic-API-
compatible, China + Global endpoints); later checkpoints add
``AnthropicProvider``, ``OpenAIProvider``, ``OllamaProvider``,
each as a new file plus a one-line entry in
:func:`magi.runtime.llm.factory.get_provider`.

Public surface re-exported here so callers don't need to know
which submodule a class lives in::

    from magi.runtime.llm import (
        LLMProvider, ChatMessage, ChatResult,
        LLMError, LLMAuthError, LLMNetworkError,
        get_provider,
    )
"""

from magi.runtime.llm.errors import (
    LLMError,
    LLMAuthError,
    LLMRateLimitError,
    LLMNetworkError,
    LLMContextLengthError,
)
from magi.runtime.llm.provider import (
    LLMProvider,
    ChatMessage,
    ChatResult,
)
from magi.runtime.llm.factory import get_provider, is_known_provider, known_providers

__all__ = [
    "LLMProvider",
    "ChatMessage",
    "ChatResult",
    "LLMError",
    "LLMAuthError",
    "LLMRateLimitError",
    "LLMNetworkError",
    "LLMContextLengthError",
    "get_provider",
    "is_known_provider",
    "known_providers",
]
